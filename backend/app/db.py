"""SQLite storage boundaries for the later annotation pipelines."""
import sqlite3
import json
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS projects (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  settings_json TEXT NOT NULL DEFAULT '{}',
  storage_rel_path TEXT NOT NULL DEFAULT '',
  archived_at TEXT,
  deleted_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  last_opened_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS media_assets (
  id TEXT PRIMARY KEY,
  project_id TEXT REFERENCES projects(id),
  filename TEXT NOT NULL,
  display_name TEXT NOT NULL DEFAULT '',
  removed_at TEXT,
  original_path TEXT NOT NULL,
  audio_path TEXT NOT NULL,
  duration_ms INTEGER NOT NULL CHECK(duration_ms >= 0),
  media_offset_ms INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS source_transcripts (
  id TEXT PRIMARY KEY, media_id TEXT NOT NULL REFERENCES media_assets(id),
  start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL,
  precision TEXT NOT NULL, text TEXT NOT NULL, language TEXT NOT NULL,
  granularity TEXT NOT NULL, provenance_json TEXT NOT NULL,
  review_status TEXT NOT NULL DEFAULT 'unreviewed',
  speech_status TEXT NOT NULL DEFAULT 'spoken',
  asr_run_id TEXT,
  asr_candidate_id TEXT,
  word_order INTEGER NOT NULL DEFAULT 0,
  edit_status TEXT NOT NULL DEFAULT 'machine',
  attention_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL DEFAULT '',
  CHECK(start_ms >= 0 AND end_ms >= start_ms)
);
CREATE TABLE IF NOT EXISTS asr_jobs (
  id TEXT PRIMARY KEY,
  media_id TEXT NOT NULL REFERENCES media_assets(id),
  model TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('queued','running','completed','failed','cancelled')),
  requested_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT,
  device TEXT,
  error TEXT,
  cancel_requested INTEGER NOT NULL DEFAULT 0,
  applied INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS asr_runs (
  id TEXT PRIMARY KEY,
  job_id TEXT NOT NULL UNIQUE REFERENCES asr_jobs(id),
  media_id TEXT NOT NULL REFERENCES media_assets(id),
  model TEXT NOT NULL,
  device TEXT NOT NULL,
  raw_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS asr_word_candidates (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES asr_runs(id),
  word_order INTEGER NOT NULL,
  raw_start_seconds REAL,
  raw_end_seconds REAL,
  text TEXT NOT NULL,
  issues_json TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS source_transcript_meta (
  media_id TEXT PRIMARY KEY REFERENCES media_assets(id),
  revision INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS alignment_previews (
  id TEXT PRIMARY KEY, media_id TEXT NOT NULL REFERENCES media_assets(id),
  revision INTEGER NOT NULL, start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL,
  engine TEXT NOT NULL DEFAULT 'stable-ts', model TEXT NOT NULL DEFAULT '',
  changes_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
  created_at TEXT NOT NULL, resolved_at TEXT
);
CREATE TABLE IF NOT EXISTS vad_jobs (
  id TEXT PRIMARY KEY, media_id TEXT NOT NULL REFERENCES media_assets(id),
  status TEXT NOT NULL CHECK(status IN ('queued','running','completed','failed','cancelled')),
  requested_at TEXT NOT NULL, started_at TEXT, finished_at TEXT,
  error TEXT, cancel_requested INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS vad_runs (
  id TEXT PRIMARY KEY, job_id TEXT NOT NULL UNIQUE REFERENCES vad_jobs(id),
  media_id TEXT NOT NULL REFERENCES media_assets(id), engine TEXT NOT NULL,
  model_version TEXT NOT NULL, parameters_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS vad_intervals (
  id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES vad_runs(id),
  media_id TEXT NOT NULL REFERENCES media_assets(id),
  kind TEXT NOT NULL CHECK(kind IN ('speech','nonspeech')),
  start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL,
  precision TEXT NOT NULL DEFAULT 'estimated', review_status TEXT NOT NULL DEFAULT 'unreviewed',
  CHECK(start_ms >= 0 AND end_ms > start_ms)
);
CREATE TABLE IF NOT EXISTS app_preferences (
  key TEXT PRIMARY KEY,
  value_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ocr_observations (
  id TEXT PRIMARY KEY, media_id TEXT NOT NULL REFERENCES media_assets(id),
  frame_ms INTEGER NOT NULL, raw_text TEXT NOT NULL,
  roi_json TEXT NOT NULL, provenance_json TEXT NOT NULL, run_id TEXT,
  CHECK(frame_ms >= 0)
);
CREATE TABLE IF NOT EXISTS ocr_jobs (
  id TEXT PRIMARY KEY, media_id TEXT NOT NULL REFERENCES media_assets(id),
  status TEXT NOT NULL CHECK(status IN ('queued','running','completed','failed','cancelled')),
  parameters_json TEXT NOT NULL, requested_at TEXT NOT NULL,
  started_at TEXT, finished_at TEXT, error TEXT,
  cancel_requested INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS ocr_runs (
  id TEXT PRIMARY KEY, job_id TEXT NOT NULL UNIQUE REFERENCES ocr_jobs(id),
  media_id TEXT NOT NULL REFERENCES media_assets(id),
  engine TEXT NOT NULL, engine_version TEXT NOT NULL,
  parameters_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ocr_job_chunks (
  id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES ocr_jobs(id),
  chunk_index INTEGER NOT NULL, core_start_ms INTEGER NOT NULL, core_end_ms INTEGER NOT NULL,
  sample_start_ms INTEGER NOT NULL, sample_end_ms INTEGER NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('queued','running','completed','failed')),
  started_at TEXT, finished_at TEXT, error TEXT,
  frames_sampled INTEGER NOT NULL DEFAULT 0, ocr_calls INTEGER NOT NULL DEFAULT 0,
  duplicates_consolidated INTEGER NOT NULL DEFAULT 0,
  UNIQUE(job_id,chunk_index)
);
CREATE TABLE IF NOT EXISTS subtitle_screen_states (
  id TEXT PRIMARY KEY, media_id TEXT NOT NULL REFERENCES media_assets(id),
  start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL,
  precision TEXT NOT NULL, text TEXT NOT NULL,
  observation_ids_json TEXT NOT NULL, provenance_json TEXT NOT NULL,
  review_status TEXT NOT NULL DEFAULT 'unreviewed', run_id TEXT,
  active INTEGER NOT NULL DEFAULT 1, sequence_id TEXT, operation_id TEXT,
  CHECK(start_ms >= 0 AND end_ms >= start_ms)
);
CREATE TABLE IF NOT EXISTS subtitle_state_operations (
  id TEXT PRIMARY KEY, media_id TEXT NOT NULL REFERENCES media_assets(id),
  run_id TEXT, kind TEXT NOT NULL, source_ids_json TEXT NOT NULL,
  result_ids_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'applied',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS target_event_reviews (
  id TEXT PRIMARY KEY, media_id TEXT NOT NULL REFERENCES media_assets(id), run_id TEXT,
  previous_state_id TEXT NOT NULL, current_state_id TEXT NOT NULL,
  source_fingerprint TEXT NOT NULL, human_event_type TEXT, review_status TEXT NOT NULL,
  notes TEXT NOT NULL DEFAULT '', snapshot_json TEXT NOT NULL DEFAULT '{}',
  invalidated INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS derived_events (
  id TEXT PRIMARY KEY, media_id TEXT NOT NULL REFERENCES media_assets(id),
  start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL,
  precision TEXT NOT NULL, event_type TEXT NOT NULL,
  before_text TEXT NOT NULL, after_text TEXT NOT NULL,
  state_ids_json TEXT NOT NULL, provenance_json TEXT NOT NULL,
  review_status TEXT NOT NULL DEFAULT 'unreviewed',
  CHECK(start_ms >= 0 AND end_ms >= start_ms)
);
CREATE TABLE IF NOT EXISTS annotation_tiers (
  id TEXT PRIMARY KEY, media_id TEXT NOT NULL REFERENCES media_assets(id),
  project_id TEXT REFERENCES projects(id),
  name TEXT NOT NULL, sort_order INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS human_annotations (
  id TEXT PRIMARY KEY, media_id TEXT NOT NULL REFERENCES media_assets(id),
  project_id TEXT REFERENCES projects(id),
  tier_id TEXT NOT NULL REFERENCES annotation_tiers(id),
  start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL,
  precision TEXT NOT NULL, text TEXT NOT NULL,
  event_label TEXT, review_status TEXT NOT NULL DEFAULT 'unreviewed',
  source_ids_json TEXT NOT NULL DEFAULT '[]',
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL DEFAULT '',
  CHECK(start_ms >= 0 AND end_ms >= start_ms)
);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize(db_path: Path) -> None:
    with closing(connect(db_path)) as connection, connection:
        connection.executescript(SCHEMA)
        # SQLite cannot retrofit NOT NULL foreign keys without rebuilding tables.
        # Add nullable project references, then assign every legacy row in one transaction.
        connection.execute("BEGIN")
        for table, columns in {
            "projects": {
                "storage_rel_path": "TEXT NOT NULL DEFAULT ''",
                "archived_at": "TEXT",
                "deleted_at": "TEXT",
            },
            "media_assets": {
                "project_id": "TEXT REFERENCES projects(id)",
                "display_name": "TEXT NOT NULL DEFAULT ''",
                "removed_at": "TEXT",
            },
            "annotation_tiers": {"project_id": "TEXT REFERENCES projects(id)"},
            "ocr_observations": {"run_id": "TEXT"},
            "subtitle_screen_states": {"run_id": "TEXT", "active": "INTEGER NOT NULL DEFAULT 1",
                                       "sequence_id": "TEXT", "operation_id": "TEXT"},
            "human_annotations": {
                "project_id": "TEXT REFERENCES projects(id)",
                "metadata_json": "TEXT NOT NULL DEFAULT '{}'",
                "created_at": "TEXT NOT NULL DEFAULT ''",
                "updated_at": "TEXT NOT NULL DEFAULT ''",
            },
            "source_transcripts": {
                "speech_status": "TEXT NOT NULL DEFAULT 'spoken'",
                "asr_run_id": "TEXT",
                "asr_candidate_id": "TEXT",
                "word_order": "INTEGER NOT NULL DEFAULT 0",
                "edit_status": "TEXT NOT NULL DEFAULT 'machine'",
                "attention_json": "TEXT NOT NULL DEFAULT '[]'",
                "created_at": "TEXT NOT NULL DEFAULT ''",
                "updated_at": "TEXT NOT NULL DEFAULT ''",
            },
            "alignment_previews": {
                "engine": "TEXT NOT NULL DEFAULT 'stable-ts'",
                "model": "TEXT NOT NULL DEFAULT ''",
            },
        }.items():
            existing = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
            for name, definition in columns.items():
                if name not in existing:
                    connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
        has_legacy = connection.execute(
            "SELECT 1 FROM media_assets WHERE project_id IS NULL LIMIT 1"
        ).fetchone()
        if has_legacy:
            now = datetime.now(timezone.utc).isoformat()
            legacy_id = str(uuid4())
            connection.execute(
                "INSERT INTO projects (id, name, settings_json, created_at, updated_at, last_opened_at) VALUES (?, ?, ?, ?, ?, ?)",
                (legacy_id, "Legacy project", json.dumps({"origin": "pre-project-workspace"}), now, now, now),
            )
            connection.execute("UPDATE media_assets SET project_id = ? WHERE project_id IS NULL", (legacy_id,))
        connection.execute(
            "UPDATE annotation_tiers SET project_id = (SELECT project_id FROM media_assets WHERE media_assets.id = annotation_tiers.media_id) WHERE project_id IS NULL"
        )
        connection.execute(
            "UPDATE human_annotations SET project_id = (SELECT project_id FROM media_assets WHERE media_assets.id = human_annotations.media_id) WHERE project_id IS NULL"
        )
        now = datetime.now(timezone.utc).isoformat()
        connection.execute(
            "UPDATE human_annotations SET created_at = ?, updated_at = ?, metadata_json = ? WHERE created_at = ''",
            (now, now, json.dumps({"migration_note": "Original creation time was not recorded."})),
        )
        if connection.execute("PRAGMA foreign_key_check").fetchone():
            raise sqlite3.IntegrityError("Foreign key check failed after project migration")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_media_project ON media_assets(project_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_annotations_media ON human_annotations(media_id, start_ms)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_source_words_media ON source_transcripts(media_id, start_ms, word_order)")
        # Legacy word_order was rewritten during edits. Link only exact, unique raw matches.
        candidates = connection.execute("SELECT id,run_id,raw_start_seconds,raw_end_seconds,text FROM asr_word_candidates").fetchall()
        matches = {}
        for candidate in candidates:
            if candidate["raw_start_seconds"] is None or candidate["raw_end_seconds"] is None:
                continue
            key = (candidate["run_id"], round(candidate["raw_start_seconds"] * 1000),
                   round(candidate["raw_end_seconds"] * 1000), candidate["text"].strip())
            matches.setdefault(key, []).append(candidate["id"])
        unlinked = connection.execute("SELECT id,asr_run_id,start_ms,end_ms,text FROM source_transcripts WHERE asr_candidate_id IS NULL AND asr_run_id IS NOT NULL").fetchall()
        editable_keys = [(word["asr_run_id"], word["start_ms"], word["end_ms"], word["text"].strip()) for word in unlinked]
        for word, key in zip(unlinked, editable_keys):
            ids = matches.get(key, [])
            if len(ids) == 1 and editable_keys.count(key) == 1:
                connection.execute("UPDATE source_transcripts SET asr_candidate_id=? WHERE id=?", (ids[0], word["id"]))
        connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_asr_one_active_job ON asr_jobs(media_id) WHERE status IN ('queued','running')")
        connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_vad_one_active_job ON vad_jobs(media_id) WHERE status IN ('queued','running')")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_vad_intervals_media ON vad_intervals(media_id,kind,start_ms)")
        connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_ocr_one_active_job ON ocr_jobs(media_id) WHERE status IN ('queued','running')")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_ocr_states_media ON subtitle_screen_states(media_id,run_id,start_ms)")
        connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_ocr_observation_frame ON ocr_observations(run_id,frame_ms) WHERE run_id IS NOT NULL")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_ocr_chunks_job ON ocr_job_chunks(job_id,chunk_index)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_target_event_reviews_media ON target_event_reviews(media_id,run_id)")
        connection.execute("INSERT OR IGNORE INTO app_preferences (key, value_json) VALUES ('asr', '{\"auto_transcribe\":true,\"default_model\":\"small\"}')")
        connection.execute("UPDATE projects SET storage_rel_path = 'projects/' || id WHERE storage_rel_path = ''")
        connection.execute("UPDATE media_assets SET display_name = filename WHERE display_name = ''")

