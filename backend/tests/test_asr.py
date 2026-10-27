import json
import sqlite3
import sys
import types
import wave
from pathlib import Path

import pytest

from app import asr, main
from app.db import connect, initialize


RAW = {"segments": [{"words": [
    {"start": 0.1, "end": 0.3, "word": "We"},
    {"start": 0.32, "end": 0.6, "word": " need,"},
    {"start": None, "end": None, "word": " uncertain"},
]}]}


class FakeEngine:
    def __init__(self, callback=None): self.callback = callback
    def transcribe(self, audio_path, model, cache_dir):
        assert audio_path.is_file() and model in asr.MODELS and cache_dir.parent.name == "models"
        if self.callback: self.callback()
        return RAW, "cpu"


@pytest.fixture
def media(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATA_DIR", tmp_path)
    monkeypatch.setattr(main, "DB_PATH", tmp_path / "simulst.sqlite3")
    initialize(main.DB_PATH)
    project = main.create_project(main.ProjectCreate(name="Synthetic ASR"))
    media_id = "synthetic-media"
    audio = tmp_path / "synthetic.wav"
    with wave.open(str(audio), "wb") as output:
        output.setnchannels(1); output.setsampwidth(2); output.setframerate(16000)
        output.writeframes(b"\0\0" * 16000)
    with connect(main.DB_PATH) as db:
        db.execute("""INSERT INTO media_assets
          (id,project_id,filename,display_name,original_path,audio_path,duration_ms,created_at)
          VALUES (?,?,?,?,?,?,?,?)""",
          (media_id, project.id, "synthetic.mp4", "synthetic.mp4", str(tmp_path / "synthetic.mp4"), str(audio), 2000, main.now_utc()))
    return media_id, tmp_path


def test_word_normalization_flags_unreliable_timing():
    words = asr.extract_words({"segments": [{"words": [
        {"start": 0.1, "end": 0.2, "word": "one"},
        {"start": 0.15, "end": 0.3, "word": "two"},
        {"start": 1.7, "end": 1.9, "word": "three"},
        {"start": None, "end": None, "word": "four"},
        {"start": 2.1, "end": 2.3, "word": "five"},
    ]}]}, 2000)
    assert words[0]["start_ms"] == 100 and words[0]["end_ms"] == 200
    assert "overlap_previous" in words[1]["issues"]
    assert "long_gap_previous" in words[2]["issues"]
    assert not words[3]["timed"] and "missing_or_invalid_timestamp" in words[3]["issues"]
    assert not words[4]["timed"] and "out_of_range_or_zero_duration" in words[4]["issues"]


def test_durable_jobs_editing_rerun_and_simple_export(media):
    media_id, private = media
    job = asr.enqueue(main.DB_PATH, media_id, "small")
    assert asr.enqueue(main.DB_PATH, media_id, "medium")["id"] == job["id"]
    assert asr.process_next_job(main.DB_PATH, private, FakeEngine())
    status = main.asr_status(media_id)["job"]
    assert status["status"] == "completed" and status["applied"] == 1 and status["device"] == "cpu"
    document = main.get_source_words(media_id)
    assert [word["text"] for word in document["words"]] == ["We", "need,"]
    assert document["candidates_needing_attention"][0]["text"] == " uncertain"
    with connect(main.DB_PATH) as db:
        raw = json.loads(db.execute("SELECT raw_json FROM asr_runs WHERE job_id=?", (job["id"],)).fetchone()[0])
    assert raw == RAW
    first, second = document["words"]
    corrected = main.put_source_words(media_id, main.SourceWordsWrite(expected_revision=document["revision"], words=[
        main.SourceWordWrite(id=first["id"], start_ms=110, end_ms=300, text="We", review_status="reviewed"),
        main.SourceWordWrite(id=second["id"], start_ms=320, end_ms=620, text="need!", review_status="reviewed"),
        main.SourceWordWrite(id="inserted-word", start_ms=700, end_ms=800, text="now"),
    ]))
    assert corrected["revision"] == document["revision"] + 1
    assert [word["text"] for word in corrected["words"]] == ["We", "need!", "now"]
    with pytest.raises(Exception) as invalid:
        main.put_source_words(media_id, main.SourceWordsWrite(expected_revision=corrected["revision"], words=[
            main.SourceWordWrite(id="outside", start_ms=1900, end_ms=2100, text="late")]))
    assert getattr(invalid.value, "status_code", None) == 422
    with pytest.raises(Exception) as conflict:
        main.put_source_words(media_id, main.SourceWordsWrite(expected_revision=document["revision"], words=[]))
    assert getattr(conflict.value, "status_code", None) == 409
    rerun = asr.enqueue(main.DB_PATH, media_id, "medium", rerun=True)
    assert asr.process_next_job(main.DB_PATH, private, FakeEngine())
    assert main.asr_status(media_id)["job"]["applied"] == 0
    assert main.get_source_words(media_id)["words"] == corrected["words"]
    assert rerun["id"] != job["id"]
    exported = main.export_source_words_simple(media_id)
    values = json.loads(exported.body)
    assert values == [{"start": 0.11, "end": 0.3, "text": "We"},
                      {"start": 0.32, "end": 0.62, "text": "need!"},
                      {"start": 0.7, "end": 0.8, "text": "now"}]
    assert all(set(item) == {"start", "end", "text"} for item in values)
    assert "synthetic_source_words.json" in exported.headers["content-disposition"]


def test_cancel_failure_and_restart_recovery(media):
    media_id, private = media
    queued = asr.enqueue(main.DB_PATH, media_id, "small")
    assert asr.cancel(main.DB_PATH, queued["id"])["status"] == "cancelled"
    assert not asr.process_next_job(main.DB_PATH, private, FakeEngine())
    running = asr.enqueue(main.DB_PATH, media_id, "small")
    engine = FakeEngine(lambda: asr.cancel(main.DB_PATH, running["id"]))
    assert asr.process_next_job(main.DB_PATH, private, engine)
    assert main.asr_status(media_id)["job"]["status"] == "cancelled"
    assert main.get_source_words(media_id)["words"] == []
    with connect(main.DB_PATH) as db:
        assert db.execute("SELECT COUNT(*) FROM asr_runs").fetchone()[0] == 0
    failed = asr.enqueue(main.DB_PATH, media_id, "small")
    class BrokenEngine:
        def transcribe(self, *_): raise RuntimeError("Synthetic model failure")
    assert asr.process_next_job(main.DB_PATH, private, BrokenEngine())
    assert main.asr_status(media_id)["job"]["status"] == "failed"
    assert main.get_source_words(media_id)["words"] == []
    interrupted = asr.enqueue(main.DB_PATH, media_id, "small")
    with connect(main.DB_PATH) as db:
        db.execute("UPDATE asr_jobs SET status='running' WHERE id=?", (interrupted["id"],))
    asr.recover_interrupted(main.DB_PATH)
    assert main.asr_status(media_id)["job"]["status"] == "failed"
    assert "restart" in main.asr_status(media_id)["job"]["error"]


def test_device_fallback_and_missing_dependency(monkeypatch, tmp_path):
    attempts = []
    class Result:
        def to_dict(self): return RAW
    class Model:
        def transcribe(self, *_args, **kwargs):
            assert kwargs["language"] == "en" and kwargs["word_timestamps"] is True
            return Result()
    def load_model(_name, device, download_root):
        attempts.append(device)
        assert Path(download_root).is_relative_to(tmp_path)
        if device == "cuda": raise RuntimeError("Synthetic GPU unavailable")
        return Model()
    monkeypatch.setitem(sys.modules, "stable_whisper", types.SimpleNamespace(load_model=load_model))
    monkeypatch.setattr(asr, "detect_device", lambda: "cuda")
    raw, device = asr.StableTsEngine().transcribe(tmp_path / "synthetic.wav", "small", tmp_path / "models")
    assert raw == RAW and device == "cpu" and attempts == ["cuda", "cpu"]
    monkeypatch.setitem(sys.modules, "stable_whisper", None)
    with pytest.raises(RuntimeError, match="not installed"):
        asr.StableTsEngine().transcribe(tmp_path / "synthetic.wav", "small", tmp_path / "models")
