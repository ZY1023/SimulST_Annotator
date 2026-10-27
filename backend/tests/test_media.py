import io
import subprocess
from pathlib import Path

import pytest
from starlette.datastructures import UploadFile
from pydantic import ValidationError

from app import main
from app.contracts import TimeSpan
from app.config import REPO_ROOT, DEFAULT_DATA_DIR, private_file, resolve_data_dir
from app.db import initialize
from app.media import seconds_to_ms


def test_seconds_to_integer_milliseconds():
    assert seconds_to_ms("1.234") == 1234
    assert seconds_to_ms(0) == 0


def test_rejects_reversed_span():
    with pytest.raises(ValidationError):
        TimeSpan(start_ms=1001, end_ms=1000)


def test_private_storage_boundary():
    assert REPO_ROOT not in DEFAULT_DATA_DIR.parents
    with pytest.raises(ValueError):
        resolve_data_dir(str(REPO_ROOT / "data"))
    with pytest.raises(ValueError):
        private_file(REPO_ROOT / "data" / "sample.mp4", DEFAULT_DATA_DIR)


def test_import_keeps_original_and_serves_audio(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATA_DIR", tmp_path)
    monkeypatch.setattr(main, "DB_PATH", tmp_path / "test.sqlite3")
    sample = tmp_path / "sample.mp4"
    subprocess.run([
        "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=160x90:r=10",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000",
        "-t", "1", "-c:v", "mpeg4", "-c:a", "aac", str(sample),
    ], check=True)
    original_bytes = sample.read_bytes()
    initialize(main.DB_PATH)
    asset = main.import_media(UploadFile(filename="sample.mp4", file=io.BytesIO(original_bytes)))
    assert asset.duration_ms == 1000
    assert asset.media_offset_ms == 0
    assert Path(main.video(asset.id).path).read_bytes() == original_bytes
    assert Path(main.audio(asset.id).path).read_bytes()[:4] == b"RIFF"
    assert len(main.list_media()) == 1
    assert main.asr_status(asset.id)["job"]["status"] == "queued"
    assert main.asr_status(asset.id)["job"]["model"] == "small"
    tiers = main.list_tiers(asset.id)
    assert [tier.kind for tier in tiers[:5]] == ["source_words", "target_characters", "subtitle_state", "disfluency", "revision"]
    custom = main.create_tier(asset.id, main.TierCreate(name="Research note"))
    assert custom.kind == "custom"
    annotation = main.create_annotation(asset.id, main.AnnotationWrite(
        tier_id=tiers[0].id, span=TimeSpan(start_ms=200, end_ms=600), text="synthetic word",
    ))
    assert annotation.id and annotation.text == "synthetic word"
    assert len(main.list_annotations(asset.id)) == 1
    updated = main.update_annotation(asset.id, annotation.id, main.AnnotationUpdate(
        span=TimeSpan(start_ms=240, end_ms=640), text="corrected synthetic word",
        event_label="example", review_status="reviewed",
    ))
    assert updated.span.start_ms == 240 and updated.review_status.value == "reviewed"
    with pytest.raises(Exception):
        main.create_annotation(asset.id, main.AnnotationWrite(
            tier_id=tiers[0].id, span=TimeSpan(start_ms=900, end_ms=1100),
        ))
    main.delete_annotation(asset.id, annotation.id)
    assert main.list_annotations(asset.id) == []
    main.put_asr_settings(main.ASRSettingsWrite(auto_transcribe=False, default_model="medium"))
    second = main.import_media(UploadFile(filename="sample-two.mp4", file=io.BytesIO(original_bytes)))
    assert main.asr_status(second.id)["job"] is None

