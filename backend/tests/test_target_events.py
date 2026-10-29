import json

from app import main, ocr, target_events
from app.db import connect, initialize
from test_asr import media
from test_ocr import FakeOCR, params


def test_visual_diff_distinguishes_incremental_and_replacement():
    assert target_events.visual_diff("我們", "我們需要")["visual_event_type"] == "addition"
    assert target_events.visual_diff("我們需要", "我們必須")["visual_event_type"] == "replacement"
    assert target_events.visual_diff("我們需要", "我們 需要")["visual_event_type"] == "layout_only"
    assert target_events.visual_diff("", "甲")["visual_event_type"] == "appearance"
    assert target_events.visual_diff("甲", "")["visual_event_type"] == "disappearance"
    first = {"id": "first", "media_id": "media", "start_ms": 1000, "end_ms": 1400,
             "text": "甲", "precision": "frame"}
    assert target_events.event_for({**first, "id": "initial-blank", "text": "", "end_ms": 1000}, first, "run")["start_ms"] == 1000


def test_event_review_persists_and_state_edit_invalidates_without_touching_raw(media):
    media_id, private = media
    (private / "synthetic.mp4").write_bytes(b"synthetic video fixture")
    main.start_target_ocr(media_id, params())
    assert ocr.process_next_job(main.DB_PATH, private, FakeOCR())
    document = main.list_target_events(media_id)
    assert [event["visual_event_type"] for event in document["events"]] == [
        "appearance", "addition", "replacement", "disappearance"]
    event = next(item for item in document["events"] if item["visual_event_type"] == "addition")
    saved = main.save_target_event_review(media_id, event["id"], main.TargetEventReviewWrite(
        run_id=document["run_id"], source_fingerprint=event["source_fingerprint"],
        human_event_type="addition", review_status="confirmed", notes="Synthetic incremental update"))
    assert saved["review_status"] == "confirmed" and not saved["invalidated"]
    initialize(main.DB_PATH)
    assert next(item for item in main.list_target_events(media_id)["events"] if item["id"] == event["id"])["notes"] == "Synthetic incremental update"
    with connect(main.DB_PATH) as db:
        project_id = db.execute("SELECT project_id FROM media_assets WHERE id=?", (media_id,)).fetchone()[0]
        before_raw = db.execute("SELECT COUNT(*) FROM ocr_observations WHERE media_id=?", (media_id,)).fetchone()[0]
    request = main.SimpleExportRequest(media_id=media_id, datasets=["target_update_events"])
    rows = json.loads(main.download_simple_export(project_id, request).body)
    assert len(rows) == 1 and rows[0]["type"] == "addition" and rows[0]["added"] == 1
    state = next(item for item in main.target_ocr_status(media_id)["states"] if item["id"] == event["current_state_id"])
    main.update_subtitle_state(media_id, state["id"], main.SubtitleStateWrite(
        start_ms=state["start_ms"], end_ms=state["end_ms"], text=state["text"], review_status="reviewed"))
    assert not next(item for item in main.list_target_events(media_id)["events"] if item["id"] == event["id"])["invalidated"]
    main.update_subtitle_state(media_id, state["id"], main.SubtitleStateWrite(
        start_ms=state["start_ms"], end_ms=state["end_ms"], text="甲丙", review_status="reviewed"))
    invalid = next(item for item in main.list_target_events(media_id)["events"] if item["id"] == event["id"])
    assert invalid["invalidated"] and invalid["notes"] == "Synthetic incremental update"
    assert json.loads(main.download_simple_export(project_id, request).body) == []
    with connect(main.DB_PATH) as db:
        assert db.execute("SELECT COUNT(*) FROM ocr_observations WHERE media_id=?", (media_id,)).fetchone()[0] == before_raw
    saved_again = main.save_target_event_review(media_id, event["id"], main.TargetEventReviewWrite(
        run_id=document["run_id"], source_fingerprint=invalid["source_fingerprint"],
        human_event_type="addition", review_status="confirmed", notes="Checked after correction"))
    assert not saved_again["invalidated"]
