import json

import pytest

from app import main, ocr
from app.db import connect, initialize
from test_asr import media


class FakeOCR:
    def scan(self, _media_path, params, cancelled):
        assert params["fps"] == 5 and not cancelled()
        return [ocr.Sample(0, ""), ocr.Sample(200, "甲", .94),
                ocr.Sample(400, "甲乙", .92), ocr.Sample(600, "甲乙", .91, True),
                ocr.Sample(800, "丙", .63), ocr.Sample(1000, "")], "synthetic-1"


def params():
    return main.OCRJobCreate(start_ms=0, end_ms=1200, fps=5,
                             roi={"x": .1, "y": .7, "width": .8, "height": .2})


def test_exact_state_changes_review_and_export_preserve_raw(media):
    media_id, private = media
    (private / "synthetic.mp4").write_bytes(b"synthetic video fixture")
    before = main.get_source_words(media_id)
    queued = main.start_target_ocr(media_id, params())
    assert queued["status"] == "queued"
    assert ocr.process_next_job(main.DB_PATH, private, FakeOCR())
    status = main.target_ocr_status(media_id)
    assert status["job"]["status"] == "completed", status["job"]["error"]
    assert [(row["start_ms"], row["end_ms"], row["text"]) for row in status["states"]] == [
        (0, 200, ""), (200, 400, "甲"), (400, 800, "甲乙"),
        (800, 1000, "丙"), (1000, 1200, "")]
    assert status["states"][3]["provenance"]["needs_review"] is True
    with connect(main.DB_PATH) as db:
        assert db.execute("SELECT COUNT(*) FROM ocr_observations WHERE media_id=?", (media_id,)).fetchone()[0] == 6
        assert db.execute("SELECT raw_text FROM ocr_observations WHERE frame_ms=400").fetchone()[0] == "甲乙"
    state = status["states"][2]
    updated = main.update_subtitle_state(media_id, state["id"],
        main.SubtitleStateWrite(start_ms=410, end_ms=790, text="人工修正", review_status="reviewed"))
    assert updated["id"] == state["id"] and updated["text"] == "人工修正"
    initialize(main.DB_PATH)
    assert next(row for row in main.target_ocr_status(media_id)["states"] if row["id"] == state["id"])["start_ms"] == 410
    with connect(main.DB_PATH) as db:
        assert db.execute("SELECT raw_text FROM ocr_observations WHERE frame_ms=400").fetchone()[0] == "甲乙"
        project_id = db.execute("SELECT project_id FROM media_assets WHERE id=?", (media_id,)).fetchone()[0]
    response = main.download_simple_export(project_id,
        main.SimpleExportRequest(media_id=media_id, datasets=["subtitle_screen_states"], format="simple_json"))
    assert json.loads(response.body) == [{"start": .410, "end": .790, "text": "人工修正"}]
    assert ocr.process_next_job(main.DB_PATH, private, FakeOCR()) is False
    main.start_target_ocr(media_id, params())
    assert ocr.process_next_job(main.DB_PATH, private, FakeOCR())
    with connect(main.DB_PATH) as db:
        assert db.execute("SELECT COUNT(*) FROM ocr_runs WHERE media_id=?", (media_id,)).fetchone()[0] == 2
        assert db.execute("SELECT text FROM subtitle_screen_states WHERE id=?", (state["id"],)).fetchone()[0] == "人工修正"
    latest = main.download_simple_export(project_id,
        main.SimpleExportRequest(media_id=media_id, datasets=["subtitle_screen_states"], format="simple_json"))
    assert json.loads(latest.body) == []  # A new unreviewed run cannot export the old reviewed text.
    assert main.get_source_words(media_id) == before


def test_tier_guards_and_reordering(media):
    media_id, _ = media
    builtins = main.list_tiers(media_id)
    with pytest.raises(Exception) as protected:
        main.delete_tier(media_id, builtins[0].id, builtins[0].name)
    assert protected.value.status_code == 409
    custom = main.create_tier(media_id, main.TierCreate(name="Synthetic custom"))
    assert main.rename_tier(media_id, custom.id, main.TierCreate(name="Renamed" )).name == "Renamed"
    reordered = main.reorder_tiers(media_id, main.TierOrder(ids=[custom.id] + [item.id for item in builtins]))
    assert reordered[0].id == custom.id
    with pytest.raises(Exception):
        main.delete_tier(media_id, custom.id, "Wrong")
    main.create_annotation(media_id, main.AnnotationWrite(tier_id=custom.id,
        span=main.TimeSpan(start_ms=100, end_ms=200, precision="estimated"), text="synthetic"))
    with pytest.raises(Exception) as occupied:
        main.delete_tier(media_id, custom.id, "Renamed")
    assert occupied.value.status_code == 409
    annotation = main.list_annotations(media_id)[0]
    main.delete_annotation(media_id, annotation.id)
    main.delete_tier(media_id, custom.id, "Renamed")
    assert all(item.id != custom.id for item in main.list_tiers(media_id))


def test_roi_and_job_cancel(media):
    media_id, _ = media
    with pytest.raises(Exception):
        main.start_target_ocr(media_id, main.OCRJobCreate(roi={"x": .8, "y": .8, "width": .4, "height": .3}))
    job = main.start_target_ocr(media_id, params())
    assert main.stop_target_ocr(media_id, job["id"])["status"] == "cancelled"
    assert not ocr.process_next_job(main.DB_PATH, main.DATA_DIR, FakeOCR())
