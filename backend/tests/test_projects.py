import csv
import io
import json
import sqlite3
import subprocess
import zipfile
from pathlib import Path

from starlette.datastructures import UploadFile

from app import main
from app.contracts import TimeSpan
from app.db import initialize


def synthetic_mp4(path: Path, frequency: int) -> bytes:
    subprocess.run([
        "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=green:s=160x90:r=10",
        "-f", "lavfi", "-i", f"sine=frequency={frequency}:sample_rate=16000",
        "-t", "1", "-c:v", "mpeg4", "-c:a", "aac", str(path),
    ], check=True)
    return path.read_bytes()


def test_project_workflow_persistence_and_exports(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATA_DIR", tmp_path)
    monkeypatch.setattr(main, "DB_PATH", tmp_path / "simulst.sqlite3")
    initialize(main.DB_PATH)
    project = main.create_project(main.ProjectCreate(name="Synthetic research"))
    assert project.storage_path == str(tmp_path / project.storage_rel_path)
    assert project.storage_rel_path.startswith("projects/Synthetic_research_")
    assert project.database_path == str(tmp_path / "simulst.sqlite3")
    first = main.import_project_media(project.id, UploadFile(
        filename="synthetic-a.mp4", file=io.BytesIO(synthetic_mp4(tmp_path / "a.mp4", 440))))
    second = main.import_project_media(project.id, UploadFile(
        filename="synthetic-b.mp4", file=io.BytesIO(synthetic_mp4(tmp_path / "b.mp4", 660))))
    assert {item.id for item in main.list_project_media(project.id)} == {first.id, second.id}
    assert Path(main.video(first.id).path).read_bytes()[:4] != b"RIFF"
    assert Path(main.audio(second.id).path).read_bytes()[:4] == b"RIFF"
    tier_a1, tier_a2 = main.list_tiers(first.id)[:2]
    tier_b = main.list_tiers(second.id)[0]
    a1 = main.create_annotation(first.id, main.AnnotationWrite(
        tier_id=tier_a1.id, span=TimeSpan(start_ms=100, end_ms=300), text='hello,\n"world"',
        metadata={"speaker": "fictional A", "source_path": "X:\\private\\synthetic.wav"},
    ))
    a2 = main.create_annotation(first.id, main.AnnotationWrite(
        tier_id=tier_a2.id, span=TimeSpan(start_ms=350, end_ms=650), text="你好",
        event_label="translation", metadata={"script": "中文"},
    ))
    b1 = main.create_annotation(second.id, main.AnnotationWrite(
        tier_id=tier_b.id, span=TimeSpan(start_ms=200, end_ms=500), text="こんにちは",
    ))
    assert a1.project_id == a2.project_id == b1.project_id == project.id
    changed = main.update_annotation(first.id, a2.id, main.AnnotationUpdate(
        span=TimeSpan(start_ms=360, end_ms=660), text="你好！", event_label="revision",
        review_status="reviewed", metadata={"script": "中文", "verified": True},
    ))
    assert changed.id == a2.id and changed.created_at == a2.created_at
    assert changed.updated_at >= a2.updated_at
    renamed = main.rename_project(project.id, main.ProjectRename(name="Renamed synthetic research"))
    assert renamed.id == project.id
    main.open_project(project.id)

    # Simulate a process restart by reopening the same SQLite file.
    initialize(main.DB_PATH)
    assert {item.id for item in main.list_project_media(project.id)} == {first.id, second.id}
    assert {item.id for item in main.list_annotations(first.id)} == {a1.id, a2.id}
    assert [item.id for item in main.list_annotations(second.id)] == [b1.id]

    whole = json.loads(main.download_export(project.id, format="json").body.decode("utf-8"))
    assert whole["project"]["name"] == "Renamed synthetic research"
    assert {item["id"] for item in whole["media_assets"]} == {first.id, second.id}
    assert {item["id"] for item in whole["annotations"]} == {a1.id, a2.id, b1.id}
    assert {item["tier_id"] for item in whole["annotations"]} == {tier_a1.id, tier_a2.id, tier_b.id}
    assert next(item for item in whole["annotations"] if item["id"] == a2.id)["metadata"] == {"script": "中文", "verified": True}
    assert next(item for item in whole["annotations"] if item["id"] == a2.id)["text"] == "你好！"
    assert next(item for item in whole["annotations"] if item["id"] == b1.id)["text"] == "こんにちは"
    assert all("original_path" not in item for item in whole["media_assets"])

    single = json.loads(main.download_export(project.id, format="json", media_id=first.id).body.decode("utf-8"))
    assert {item["id"] for item in single["annotations"]} == {a1.id, a2.id}
    csv_response = main.download_export(project.id, format="csv", media_id=first.id)
    csv_rows = list(csv.DictReader(io.StringIO(csv_response.body.decode("utf-8-sig"))))
    assert {row["annotation_id"] for row in csv_rows} == {a1.id, a2.id}
    assert next(row for row in csv_rows if row["annotation_id"] == a2.id)["text"] == "你好！"
    with zipfile.ZipFile(io.BytesIO(main.download_export(project.id, format="csv").body)) as archive:
        names = set(archive.namelist())
        assert {"annotations.csv", "media.csv", "tiers.csv", "project.json"} <= names
        rows = list(csv.DictReader(io.StringIO(archive.read("annotations.csv").decode("utf-8-sig"))))
        assert {row["annotation_id"] for row in rows} == {a1.id, a2.id, b1.id}
        assert next(row for row in rows if row["annotation_id"] == b1.id)["text"] == "こんにちは"

    with main.connect(main.DB_PATH) as connection:
        connection.execute(
            """INSERT INTO source_transcripts
               (id, media_id, start_ms, end_ms, precision, text, language, granularity, provenance_json, review_status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("synthetic-source", first.id, 100, 250, "estimated", "fictional source",
             "en", "word", '{"method":"synthetic","model":null,"confidence":1.0}', "unreviewed"),
        )
        connection.execute(
            """INSERT INTO ocr_observations
               (id, media_id, frame_ms, raw_text, roi_json, provenance_json)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("synthetic-ocr", first.id, 300, "字幕 A", '{"x":0,"y":0.7,"width":1,"height":0.3}',
             '{"method":"synthetic","confidence":0.9}'),
        )
        connection.execute(
            """INSERT INTO subtitle_screen_states
               (id, media_id, start_ms, end_ms, precision, text, observation_ids_json, provenance_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            ("synthetic-state", first.id, 300, 400, "frame", "字幕 A", '["synthetic-ocr"]',
             '{"method":"synthetic","confidence":0.9}'),
        )
        connection.execute(
            """INSERT INTO derived_events
               (id, media_id, start_ms, end_ms, precision, event_type, before_text, after_text,
                state_ids_json, provenance_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("synthetic-change", first.id, 300, 400, "frame", "appearance", "", "字幕 A",
             '["synthetic-state","synthetic-state"]', '{"method":"synthetic"}'),
        )
    options = main.export_options(project.id, first.id)
    assert {tier["id"]: tier["record_count"] for tier in options["tiers"]}[tier_a2.id] == 1
    assert next(item for item in options["research_data"] if item["name"] == "source_transcripts")["record_count"] == 1
    assert {item["name"]: item["record_count"] for item in options["research_data"]} == {
        "source_transcripts": 1, "ocr_observations": 1,
        "subtitle_screen_states": 1, "derived_events": 1,
    }
    one_tier = main.download_advanced_export(project.id, main.AdvancedExportRequest(
        media_id=first.id, tier_ids=[tier_a2.id], format="json", structure="separate"))
    assert one_tier.media_type == "application/json"
    assert [item["id"] for item in json.loads(one_tier.body)["annotations"]] == [a2.id]
    redacted = main.download_advanced_export(project.id, main.AdvancedExportRequest(
        media_id=first.id, tier_ids=[tier_a1.id], format="json", structure="combined"))
    assert "source_path" not in redacted.body.decode("utf-8")
    multiple = main.download_advanced_export(project.id, main.AdvancedExportRequest(
        tier_ids=[tier_a1.id, tier_a2.id], format="csv", structure="separate"))
    with zipfile.ZipFile(io.BytesIO(multiple.body)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["record_counts"]["human_annotations"] == 2
        tier_files = [name for name in archive.namelist() if name.endswith(".csv")]
        assert len(tier_files) == 2
        parsed = [row for name in tier_files for row in csv.DictReader(io.StringIO(archive.read(name).decode("utf-8-sig")))]
        assert len(parsed) == 2
        assert next(row for row in parsed if row["record_id"] == a1.id)["text"] == 'hello,\n"world"'
        assert next(row for row in parsed if row["record_id"] == a2.id)["start_ms"] == "360"
    research_only = main.download_advanced_export(project.id, main.AdvancedExportRequest(
        research_data=["source_transcripts"], format="json", structure="combined"))
    selected = json.loads(research_only.body)
    assert len(selected["source_transcripts"]) == 1
    assert selected["annotations"] == [] and selected["ocr_observations"] == []
    states_and_changes = json.loads(main.download_advanced_export(project.id, main.AdvancedExportRequest(
        media_id=first.id, research_data=["ocr_observations", "subtitle_screen_states", "derived_events"],
        format="json", structure="combined")).body)
    assert states_and_changes["source_transcripts"] == []
    assert [row["id"] for row in states_and_changes["ocr_observations"]] == ["synthetic-ocr"]
    assert [row["id"] for row in states_and_changes["subtitle_screen_states"]] == ["synthetic-state"]
    assert [row["id"] for row in states_and_changes["derived_events"]] == ["synthetic-change"]
    research_csv = main.download_advanced_export(project.id, main.AdvancedExportRequest(
        research_data=["source_transcripts"], format="csv", structure="combined"))
    research_rows = list(csv.DictReader(io.StringIO(research_csv.body.decode("utf-8-sig"))))
    assert len(research_rows) == 1 and research_rows[0]["record_type"] == "source_transcripts"
    assert research_rows[0]["start_ms"] == "100" and research_rows[0]["end_ms"] == "250"
    empty_tier = next(tier for tier in options["tiers"] if tier["record_count"] == 0)
    empty_export = main.download_advanced_export(project.id, main.AdvancedExportRequest(
        media_id=first.id, tier_ids=[empty_tier["id"]], include_empty_tiers=True))
    assert json.loads(empty_export.body)["annotations"] == []
    both = main.download_advanced_export(project.id, main.AdvancedExportRequest(
        tier_ids=[tier_a1.id, tier_a2.id], research_data=["source_transcripts"],
        format="both", structure="both"))
    with zipfile.ZipFile(io.BytesIO(both.body)) as archive:
        assert "combined/all_annotations.json" in archive.namelist()
        assert "combined/all_annotations.csv" in archive.namelist()
        assert "research_data/source_transcripts.json" in archive.namelist()
        assert len(archive.namelist()) >= 8

    storage = main.project_storage(project.id)
    assert storage["media_count"] == 2 and storage["annotation_count"] == 3
    assert storage["research_counts"]["source_transcripts"] == 1
    assert all(item["reference_count"] == 1 for item in storage["files"])
    assert any(item["category"] == "potentially_orphaned" for item in main.storage_audit()["files"])
    renamed_media = main.rename_media(first.id, main.MediaRename(display_name="Synthetic A"))
    assert renamed_media.display_name == "Synthetic A" and renamed_media.filename == "synthetic-a.mp4"
    removed_media = main.remove_media(first.id)
    assert removed_media.removed_at and len(main.list_project_media(project.id)) == 1
    assert len(main.list_annotations(first.id)) == 2
    assert main.restore_media(first.id).removed_at is None
    assert main.archive_project(project.id).archived_at
    assert all(item.id != project.id for item in main.list_projects())
    assert main.restore_project(project.id).archived_at is None
    assert main.delete_project(project.id, main.ConfirmDelete(confirm_name="Renamed synthetic research")).deleted_at
    assert len(main.list_project_media(project.id)) == 2
    assert Path(main.video(first.id).path).exists()
    assert main.restore_project(project.id).deleted_at is None
    unicode_project = main.create_project(main.ProjectCreate(name="中文研究"))
    unicode_export = main.download_advanced_export(unicode_project.id, main.AdvancedExportRequest(
        research_data=["ocr_observations"], format="json", structure="combined"))
    assert "filename*=UTF-8''" in unicode_export.headers["content-disposition"]


def test_legacy_database_migration_preserves_annotation(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE media_assets (id TEXT PRIMARY KEY, filename TEXT NOT NULL,
              original_path TEXT NOT NULL, audio_path TEXT NOT NULL, duration_ms INTEGER NOT NULL,
              media_offset_ms INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
            CREATE TABLE annotation_tiers (id TEXT PRIMARY KEY, media_id TEXT NOT NULL,
              name TEXT NOT NULL, sort_order INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE human_annotations (id TEXT PRIMARY KEY, media_id TEXT NOT NULL,
              tier_id TEXT NOT NULL, start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL,
              precision TEXT NOT NULL, text TEXT NOT NULL, event_label TEXT,
              review_status TEXT NOT NULL DEFAULT 'unreviewed', source_ids_json TEXT NOT NULL DEFAULT '[]');
            INSERT INTO media_assets VALUES ('m1','fictional.mp4','original.mp4','audio.wav',1000,0,'2026-01-01');
            INSERT INTO annotation_tiers VALUES ('t1','m1','Source words',0);
            INSERT INTO human_annotations VALUES ('a1','m1','t1',100,300,'estimated','synthetic text',NULL,'reviewed','[]');
        """)
    initialize(path)
    initialize(path)  # migration is idempotent
    with sqlite3.connect(path) as connection:
        project_id = connection.execute("SELECT project_id FROM media_assets WHERE id='m1'").fetchone()[0]
        annotation = connection.execute(
            "SELECT project_id, media_id, tier_id, start_ms, end_ms, text, metadata_json FROM human_annotations WHERE id='a1'"
        ).fetchone()
        assert annotation[:6] == (project_id, "m1", "t1", 100, 300, "synthetic text")
        assert "migration_note" in json.loads(annotation[6])
        assert connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 1
        assert connection.execute("SELECT storage_rel_path FROM projects").fetchone()[0].startswith("projects/")
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
