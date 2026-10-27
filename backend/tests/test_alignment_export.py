import io
import json
import zipfile

import pytest

from app import asr, main
from app.asr import AlignmentChange
from app.db import connect, initialize
from test_asr import FakeEngine, media  # synthetic media fixture


def test_stable_raw_link_diagnostics_and_preview_apply_undo(media, monkeypatch):
    media_id, private = media
    asr.enqueue(main.DB_PATH, media_id, "small")
    assert asr.process_next_job(main.DB_PATH, private, FakeEngine())
    original = main.get_source_words(media_id)
    words = original["words"]
    assert all(word["asr_candidate_id"] for word in words)
    diagnostics = main.alignment_diagnostics(media_id)
    assert diagnostics["audio_sample_rate_hz"] == 16000
    assert diagnostics["editable_issues"]["linked_raw_words"] == 2
    class FakeAligner:
        def preview(self, _audio, selected, _start, _end, _model, _cache):
            return [AlignmentChange(word["id"], word["text"], word["start_ms"], word["end_ms"],
                                    word["start_ms"] + 10, word["end_ms"] + 10) for word in selected]
    monkeypatch.setattr(main, "StableTsWordAligner", FakeAligner)
    preview = main.preview_alignment(media_id, main.AlignmentPreviewRequest(start_ms=0, end_ms=1000,
        expected_revision=original["revision"]))
    assert preview["changes"][0]["old_start_ms"] == 100
    assert main.get_source_words(media_id)["words"] == words  # preview is read only
    applied = main.apply_alignment(media_id, preview["id"], main.AlignmentApplyRequest())
    assert [word["start_ms"] for word in applied["words"]] == [110, 330]
    assert [word["text"] for word in applied["words"]] == ["We", "need,"]
    assert [word["asr_candidate_id"] for word in applied["words"]] == [word["asr_candidate_id"] for word in words]
    with connect(main.DB_PATH) as db:
        assert db.execute("SELECT status FROM alignment_previews WHERE id=?", (preview["id"],)).fetchone()[0] == "applied"
    # The editor's undo restores the saved word snapshot through the same revisioned PUT.
    undone = main.put_source_words(media_id, main.SourceWordsWrite(expected_revision=applied["revision"], words=[
        main.SourceWordWrite(id=word["id"], start_ms=word["start_ms"], end_ms=word["end_ms"], text=word["text"])
        for word in words]))
    assert [word["start_ms"] for word in undone["words"]] == [100, 320]


def test_reviewed_alignment_requires_confirmation_and_reject(media, monkeypatch):
    media_id, private = media
    asr.enqueue(main.DB_PATH, media_id, "small")
    asr.process_next_job(main.DB_PATH, private, FakeEngine())
    old = main.get_source_words(media_id)
    edited = main.put_source_words(media_id, main.SourceWordsWrite(expected_revision=old["revision"], words=[
        main.SourceWordWrite(id=word["id"], start_ms=word["start_ms"], end_ms=word["end_ms"],
                             text=word["text"], review_status="reviewed") for word in old["words"]]))
    class FakeAligner:
        def preview(self, _audio, selected, _start, _end, _model, _cache):
            return [AlignmentChange(word["id"], word["text"], word["start_ms"], word["end_ms"],
                                    word["start_ms"] + 10, word["end_ms"] + 10) for word in selected]
    monkeypatch.setattr(main, "StableTsWordAligner", FakeAligner)
    preview = main.preview_alignment(media_id, main.AlignmentPreviewRequest(start_ms=0, end_ms=1000,
        expected_revision=edited["revision"]))
    with pytest.raises(Exception) as blocked:
        main.apply_alignment(media_id, preview["id"], main.AlignmentApplyRequest())
    assert blocked.value.status_code == 409
    assert main.reject_alignment(media_id, preview["id"]) == {"status": "rejected"}
    assert main.get_source_words(media_id)["words"] == edited["words"]


def test_selected_exports_are_exact_and_preview_matches(media):
    media_id, private = media
    asr.enqueue(main.DB_PATH, media_id, "small")
    asr.process_next_job(main.DB_PATH, private, FakeEngine())
    with connect(main.DB_PATH) as db:
        project_id = db.execute("SELECT project_id FROM media_assets WHERE id=?", (media_id,)).fetchone()[0]
    one = main.SimpleExportRequest(media_id=media_id, datasets=["source_transcripts"], format="simple_json")
    preview = main.preview_simple_export(project_id, one)
    response = main.download_simple_export(project_id, one)
    values = json.loads(response.body)
    assert preview["filename"] == "source_words.json"
    assert preview["record_count"] == len(values) == 2
    assert all(set(value) == {"start", "end", "text"} for value in values)
    assert values[0] == {"start": .1, "end": .3, "text": "We"}
    multi = main.SimpleExportRequest(media_id=media_id, datasets=["source_transcripts", "derived_events"])
    archive = zipfile.ZipFile(io.BytesIO(main.download_simple_export(project_id, multi).body))
    assert set(archive.namelist()) == {"source_words.json", "text_change_events.json"}
    assert json.loads(archive.read("text_change_events.json")) == []
    detailed = main.SimpleExportRequest(media_id=media_id, datasets=["source_transcripts"], format="detailed_json")
    assert "id" in json.loads(main.download_simple_export(project_id, detailed).body)[0]
    project_scope = main.SimpleExportRequest(datasets=["source_transcripts"])
    project_zip = zipfile.ZipFile(io.BytesIO(main.download_simple_export(project_id, project_scope).body))
    assert len(project_zip.namelist()) == 1
    assert project_zip.namelist()[0].endswith("/source_words.json")
    complete = json.loads(main.download_simple_export(project_id, main.SimpleExportRequest(complete_project=True)).body)
    assert len(complete["asr_runs"]) == 1
    assert len(complete["asr_word_candidates"]) == 3
    assert "alignment_previews" in complete
    assert "source_transcripts" in json.loads(main.download_export(project_id, format="json").body)
