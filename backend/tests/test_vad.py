import json

from app import asr, main, vad
from app.db import connect, initialize
from test_asr import FakeEngine, media


class FakeVAD:
    def detect(self, _audio_path, cancelled):
        assert not cancelled()
        return [(100, 400), (390, 600), (900, 1100)], "synthetic-1"


def test_normalization_creates_nonoverlapping_speech_and_nonspeech():
    assert vad.normalize_intervals([(100, 400), (390, 600), (900, 1100)], 1200) == [
        ("nonspeech", 0, 100), ("speech", 100, 600),
        ("nonspeech", 600, 900), ("speech", 900, 1100), ("nonspeech", 1100, 1200)]


def test_vad_job_persists_and_diagnostics_use_only_review_flags(media):
    media_id, private = media
    job = vad.enqueue(main.DB_PATH, media_id)
    assert vad.enqueue(main.DB_PATH, media_id)["id"] == job["id"]
    assert vad.process_next_job(main.DB_PATH, private, FakeVAD())
    state = main.vad_status(media_id)
    assert state["job"]["status"] == "completed"
    assert state["run"]["engine"] == "silero-vad"
    assert state["run"]["model_version"] == "synthetic-1"
    assert state["run"]["parameters"] == vad.PARAMETERS
    assert [(item["kind"], item["start_ms"], item["end_ms"]) for item in state["intervals"]] == [
        ("nonspeech", 0, 100), ("speech", 100, 600), ("nonspeech", 600, 900),
        ("speech", 900, 1100), ("nonspeech", 1100, 2000)]
    initialize(main.DB_PATH)
    assert main.vad_status(media_id)["intervals"] == state["intervals"]
    first_run_id = state["run"]["id"]
    vad.enqueue(main.DB_PATH, media_id)
    assert vad.process_next_job(main.DB_PATH, private, FakeVAD())
    assert main.vad_status(media_id)["run"]["id"] != first_run_id
    with connect(main.DB_PATH) as db:
        assert db.execute("SELECT COUNT(*) FROM vad_runs WHERE media_id=?", (media_id,)).fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM vad_intervals WHERE run_id=?", (first_run_id,)).fetchone()[0] == 5
    asr.enqueue(main.DB_PATH, media_id, "small")
    asr.process_next_job(main.DB_PATH, private, FakeEngine())
    before = main.get_source_words(media_id)
    report = main.alignment_diagnostics(media_id)
    assert report["vad_run_id"] == main.vad_status(media_id)["run"]["id"]
    assert all(item["speech_overlap_ms"] + item["nonspeech_overlap_ms"] == item["duration_ms"]
               for item in report["word_details"])
    assert main.get_source_words(media_id) == before  # diagnostic never edits source words


def test_cancel_and_hallucination_marking_preserve_raw(media):
    media_id, private = media
    queued = vad.enqueue(main.DB_PATH, media_id)
    assert vad.cancel(main.DB_PATH, queued["id"])["status"] == "cancelled"
    assert not vad.process_next_job(main.DB_PATH, private, FakeVAD())
    asr.enqueue(main.DB_PATH, media_id, "small")
    asr.process_next_job(main.DB_PATH, private, FakeEngine())
    original = main.get_source_words(media_id)
    first, second = original["words"]
    edited = main.put_source_words(media_id, main.SourceWordsWrite(expected_revision=original["revision"], words=[
        main.SourceWordWrite(id=first["id"], start_ms=first["start_ms"], end_ms=first["end_ms"],
                             text=first["text"], review_status="rejected", speech_status="not_spoken"),
        main.SourceWordWrite(id=second["id"], start_ms=second["start_ms"], end_ms=second["end_ms"],
                             text="corrected", review_status="reviewed")]))
    assert edited["words"][0]["speech_status"] == "not_spoken"
    assert json.loads(main.export_source_words_simple(media_id).body) == [
        {"start": .32, "end": .6, "text": "corrected"}]
    with connect(main.DB_PATH) as db:
        raw = db.execute("SELECT text FROM asr_word_candidates WHERE id=?", (first["asr_candidate_id"],)).fetchone()[0]
    assert raw.strip() == first["text"]
