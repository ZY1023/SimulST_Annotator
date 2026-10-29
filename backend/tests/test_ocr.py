import json

import pytest

from app import main, ocr, subtitle_states
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


class ChunkOCR:
    def __init__(self, fail_once_at=None):
        self.calls = []
        self.fail_once_at = fail_once_at

    def scan(self, _media_path, params, cancelled):
        self.calls.append(params["start_ms"])
        if self.fail_once_at is not None and params["start_ms"] >= self.fail_once_at:
            self.fail_once_at = None
            raise RuntimeError("synthetic chunk failure")
        assert not cancelled()
        def text_at(ms):
            return "甲" if ms < 30000 else "乙" if ms < 60000 else ""
        return [ocr.Sample(ms, text_at(ms), .95) for ms in range(params["start_ms"], params["end_ms"], 200)], "synthetic-chunk"


def test_entire_video_chunks_resume_deduplicate_and_select_run(media):
    media_id, private = media
    (private / "synthetic.mp4").write_bytes(b"synthetic video fixture")
    with connect(main.DB_PATH) as db:
        db.execute("UPDATE media_assets SET duration_ms=65000 WHERE id=?", (media_id,))
    job = main.start_target_ocr(media_id, main.OCRJobCreate(mode="entire", fps=5,
        chunk_ms=30000, overlap_ms=200, roi={"x": .1, "y": .7, "width": .8, "height": .2}))
    engine = ChunkOCR(fail_once_at=29000)
    assert ocr.process_next_job(main.DB_PATH, private, engine)
    failed = main.target_ocr_status(media_id)
    assert failed["job"]["status"] == "failed"
    assert [chunk["status"] for chunk in failed["chunks"]] == ["completed", "failed", "queued"]
    with connect(main.DB_PATH) as db:
        first_count = db.execute("SELECT COUNT(*) FROM ocr_observations WHERE media_id=?", (media_id,)).fetchone()[0]
    assert first_count > 0
    assert main.resume_target_ocr(media_id, job["id"])["status"] == "queued"
    assert ocr.process_next_job(main.DB_PATH, private, engine)
    result = main.target_ocr_status(media_id)
    assert result["job"]["status"] == "completed"
    assert len(result["chunks"]) == 3
    assert result["summary"]["frames_sampled"] == 325
    assert result["summary"]["duplicate_observations_consolidated"] > 0
    assert [(s["start_ms"], s["end_ms"], s["text"]) for s in result["states"]] == [
        (0, 30000, "甲"), (30000, 60000, "乙"), (60000, 65000, "")]
    assert engine.calls.count(0) == 1  # Successful first chunk was not rescanned.
    assert main.target_ocr_status(media_id, result["run"]["id"])["run"]["id"] == result["run"]["id"]
    assert main.target_ocr_status(media_id, result["run"]["id"])["job"]["id"] == job["id"]


def test_recover_interrupted_chunk_and_stabilization(media):
    media_id, private = media
    (private / "synthetic.mp4").write_bytes(b"synthetic video fixture")
    job = main.start_target_ocr(media_id, params())
    with connect(main.DB_PATH) as db:
        db.execute("UPDATE ocr_jobs SET status='running' WHERE id=?", (job["id"],))
        db.execute("UPDATE ocr_job_chunks SET status='running' WHERE job_id=?", (job["id"],))
    ocr.recover_interrupted(main.DB_PATH)
    assert main.target_ocr_status(media_id)["job"]["status"] == "queued"
    assert ocr.process_next_job(main.DB_PATH, private, FakeOCR())
    samples = [ocr.Sample(0, "我們需要", .9), ocr.Sample(200, "我們需妥", .3),
               ocr.Sample(400, "我們需要", .9), ocr.Sample(600, "我們必須", .95)]
    stable, changed = ocr.stabilize_samples(samples)
    assert changed == 1 and stable[1].text == "我們需要"
    assert ocr.stabilize_samples([ocr.Sample(0, "甲", .9), ocr.Sample(200, "乙", .95),
                                  ocr.Sample(400, "甲", .9)])[1] == 0
    assert ocr.normalize_text("我 們\n需要") == "我們需要"


def test_state_merge_split_group_and_undo_keep_raw(media):
    media_id, private = media
    (private / "synthetic.mp4").write_bytes(b"synthetic video fixture")
    main.start_target_ocr(media_id, params())
    assert ocr.process_next_job(main.DB_PATH, private, FakeOCR())
    run_id = main.target_ocr_status(media_id)["run"]["id"]
    a = main.insert_subtitle_state(media_id, main.SubtitleStateInsert(run_id=run_id, start_ms=1200, end_ms=1400, text="我們需要"))
    b = main.insert_subtitle_state(media_id, main.SubtitleStateInsert(run_id=run_id, start_ms=1400, end_ms=1600, text="我們需要"))
    preview = main.preview_subtitle_merge(media_id, main.SubtitleSelection(state_ids=[a["id"], b["id"]]))
    assert preview["equivalent"] and preview["end_ms"] == 1600
    merged = main.merge_subtitle_states(media_id, main.SubtitleMerge(state_ids=[a["id"], b["id"]]))
    assert merged["state"]["text"] == "我們需要"
    assert all(item["id"] not in (a["id"], b["id"]) for item in main.target_ocr_status(media_id)["states"])
    main.undo_subtitle_operation(media_id, merged["operation_id"])
    assert all(any(item["id"] == sid for item in main.target_ocr_status(media_id)["states"]) for sid in (a["id"], b["id"]))
    main.redo_subtitle_operation(media_id, merged["operation_id"])
    split = main.split_subtitle_state(media_id, merged["state"]["id"], main.SubtitleSplit(split_ms=1500))
    assert len(split["states"]) == 2
    group = main.group_subtitle_states(media_id, main.SubtitleSelection(state_ids=[s["id"] for s in split["states"]]))
    assert group["sequence_id"]
    with connect(main.DB_PATH) as db:
        assert db.execute("SELECT raw_text FROM ocr_observations WHERE frame_ms=400").fetchone()[0] == "甲乙"


def test_simple_export_uses_selected_run_and_explicit_review_filters(media):
    media_id, private = media
    (private / "synthetic.mp4").write_bytes(b"synthetic video fixture")
    main.start_target_ocr(media_id, params())
    assert ocr.process_next_job(main.DB_PATH, private, FakeOCR())
    first = main.target_ocr_status(media_id)
    first_run = first["run"]["id"]
    for state in first["states"]:
        if state["text"]:
            main.update_subtitle_state(media_id, state["id"], main.SubtitleStateWrite(
                start_ms=state["start_ms"], end_ms=state["end_ms"], text=state["text"], review_status="reviewed"))
    main.start_target_ocr(media_id, params())
    assert ocr.process_next_job(main.DB_PATH, private, FakeOCR())
    assert main.target_ocr_status(media_id, first_run)["job"]["id"] == first["job"]["id"]
    with connect(main.DB_PATH) as db:
        project_id = db.execute("SELECT project_id FROM media_assets WHERE id=?", (media_id,)).fetchone()[0]
    request = main.SimpleExportRequest(media_id=media_id, datasets=["subtitle_screen_states"],
                                       ocr_run_id=first_run)
    rows = json.loads(main.download_simple_export(project_id, request).body)
    assert [row["text"] for row in rows] == ["甲", "甲乙", "丙"]
    assert all(set(row) == {"start", "end", "text"} for row in rows)
    latest = main.SimpleExportRequest(media_id=media_id, datasets=["subtitle_screen_states"])
    assert json.loads(main.download_simple_export(project_id, latest).body) == []
    latest.include_unreviewed = True
    assert len(json.loads(main.download_simple_export(project_id, latest).body)) == 3
    metrics = main.SimpleExportRequest(media_id=media_id, datasets=["target_update_events", "output_gap_candidates"],
                                       ocr_run_id=first_run, gap_threshold_ms=200)
    archive = __import__("zipfile").ZipFile(__import__("io").BytesIO(main.download_simple_export(project_id, metrics).body))
    assert set(archive.namelist()) == {"target_update_events.json", "output_gap_candidates.json"}
    assert len(json.loads(archive.read("target_update_events.json"))) == 2
    assert all("duration" in row for row in json.loads(archive.read("output_gap_candidates.json")))
    complete = json.loads(main.download_simple_export(project_id,
        main.SimpleExportRequest(complete_project=True)).body)
    assert len(complete["ocr_job_chunks"]) == 2
    assert "subtitle_state_operations" in complete
