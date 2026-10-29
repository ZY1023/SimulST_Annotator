import csv
import io
import json
import os
import re
import shutil
import sqlite3
import subprocess
import zipfile
from contextlib import asynccontextmanager, closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import quote
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from .config import private_file, resolve_data_dir
from .contracts import HumanAnnotation, MediaAsset, Project, ReviewStatus, TimePrecision, TimeSpan
from .db import connect, initialize
from .media import extract_audio, probe_duration_ms
from .storage import inventory
from .exports import RESEARCH_TYPES, build_advanced_export, redact_private_values
from .asr import ASRWorker, MODELS, cancel as cancel_asr_job, detect_device, enqueue as enqueue_asr, preferences as asr_preferences
from .alignment import StableTsWordAligner, timing_diagnostics
from .simple_exports import DATASETS as SIMPLE_DATASETS, build_files
from .vad import VADWorker, enqueue as enqueue_vad, cancel as cancel_vad_job
from .ocr import OCRWorker, enqueue as enqueue_ocr, cancel as cancel_ocr_job, validate_roi

DATA_DIR = resolve_data_dir()
DB_PATH = DATA_DIR / "simulst.sqlite3"

DEFAULT_TIERS = (
    ("source_words", "Source words"),
    ("target_characters", "Target characters"),
    ("subtitle_state", "Subtitle state"),
    ("disfluency", "Disfluency"),
    ("revision", "Revision"),
)


class Tier(BaseModel):
    id: str
    project_id: str
    media_id: str
    name: str
    kind: str
    sort_order: int


class TierCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class TierOrder(BaseModel):
    ids: list[str]


class OCRJobCreate(BaseModel):
    start_ms: int = Field(default=0, ge=0)
    end_ms: int | None = Field(default=None, gt=0)
    fps: int = Field(default=5, ge=1, le=15)
    roi: dict[str, float]
    crop_change_threshold: float = Field(default=1.2, ge=0, le=30)
    confidence_review_threshold: float = Field(default=0.75, ge=0, le=1)


class SubtitleStateWrite(BaseModel):
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    text: str = Field(max_length=10000)
    review_status: ReviewStatus


class AnnotationWrite(BaseModel):
    tier_id: str
    span: TimeSpan
    text: str = Field(default="", max_length=10000)
    event_label: str | None = Field(default=None, max_length=120)
    review_status: ReviewStatus = ReviewStatus.unreviewed
    metadata: dict = Field(default_factory=dict)


class AnnotationUpdate(BaseModel):
    span: TimeSpan
    text: str = Field(max_length=10000)
    event_label: str | None = Field(default=None, max_length=120)
    review_status: ReviewStatus
    metadata: dict | None = None


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class ProjectRename(ProjectCreate):
    pass


class ConfirmDelete(BaseModel):
    confirm_name: str


class MediaRename(BaseModel):
    display_name: str = Field(min_length=1, max_length=120)


class AdvancedExportRequest(BaseModel):
    media_id: str | None = None
    tier_ids: list[str] = Field(default_factory=list)
    research_data: list[Literal["source_transcripts", "ocr_observations", "subtitle_screen_states", "derived_events", "vad_intervals"]] = Field(default_factory=list)
    format: Literal["json", "csv", "both"] = "json"
    structure: Literal["separate", "combined", "both"] = "combined"
    include_empty_tiers: bool = False


class SimpleExportRequest(BaseModel):
    media_id: str | None = None
    datasets: list[str] = Field(default_factory=list)
    format: Literal["simple_json", "csv", "detailed_json"] = "simple_json"
    complete_project: bool = False


class ASRSettingsWrite(BaseModel):
    auto_transcribe: bool
    default_model: Literal["small", "medium"]


class ASRJobCreate(BaseModel):
    model: Literal["small", "medium"] | None = None
    rerun: bool = False


class AlignmentPreviewRequest(BaseModel):
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    expected_revision: int = Field(ge=0)
    model: Literal["small", "medium"] = "small"


class AlignmentApplyRequest(BaseModel):
    confirm_reviewed: bool = False


class SourceWordWrite(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    text: str = Field(min_length=1, max_length=500)
    precision: TimePrecision = TimePrecision.estimated
    review_status: ReviewStatus = ReviewStatus.unreviewed
    speech_status: Literal["spoken", "not_spoken", "uncertain"] = "spoken"


class SourceWordsWrite(BaseModel):
    expected_revision: int = Field(ge=0)
    words: list[SourceWordWrite]


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def project_folder(project_id: str) -> Path:
    with connect(DB_PATH) as connection:
        row = connection.execute("SELECT storage_rel_path FROM projects WHERE id = ?", (project_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return private_file(DATA_DIR / row["storage_rel_path"], DATA_DIR)


def safe_name(name: str) -> str:
    return re.sub(r"[^\w-]+", "_", name, flags=re.UNICODE).strip("._")[:48] or "Project"


def project_contract(row: sqlite3.Row) -> Project:
    return Project(
        id=row["id"], name=row["name"], settings=json.loads(row["settings_json"]),
        storage_path=str(private_file(DATA_DIR / row["storage_rel_path"], DATA_DIR)),
        storage_rel_path=row["storage_rel_path"], database_path=str(DB_PATH),
        archived_at=row["archived_at"], deleted_at=row["deleted_at"], created_at=row["created_at"],
        updated_at=row["updated_at"], last_opened_at=row["last_opened_at"],
    )


def find_project(project_id: str) -> sqlite3.Row:
    with connect(DB_PATH) as connection:
        row = connection.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return row


def create_project_record(name: str) -> Project:
    name = name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Project name cannot be blank")
    project_id, timestamp = str(uuid4()), now_utc()
    storage_rel_path = f"projects/{safe_name(name)}_{project_id[:6]}"
    folder = private_file(DATA_DIR / storage_rel_path, DATA_DIR)
    (folder / "media").mkdir(parents=True, exist_ok=True)
    (folder / "derived" / "audio").mkdir(parents=True, exist_ok=True)
    (folder / "exports").mkdir(parents=True, exist_ok=True)
    with connect(DB_PATH) as connection:
        connection.execute(
            "INSERT INTO projects (id, name, settings_json, storage_rel_path, created_at, updated_at, last_opened_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (project_id, name, "{}", storage_rel_path, timestamp, timestamp, timestamp),
        )
        row = connection.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    return project_contract(row)


def compatibility_project_id() -> str:
    with connect(DB_PATH) as connection:
        row = connection.execute("SELECT id FROM projects WHERE deleted_at IS NULL ORDER BY created_at LIMIT 1").fetchone()
    return row[0] if row else create_project_record("Legacy project").id


@asynccontextmanager
async def lifespan(_app: FastAPI):
    initialize(DB_PATH)
    worker = ASRWorker(DB_PATH, DATA_DIR)
    vad_worker = VADWorker(DB_PATH, DATA_DIR)
    ocr_worker = OCRWorker(DB_PATH, DATA_DIR)
    worker.start()
    vad_worker.start()
    ocr_worker.start()
    try:
        yield
    finally:
        worker.stop()
        vad_worker.stop()
        ocr_worker.stop()


app = FastAPI(title="SimulST Annotator API", version="0.1.0", lifespan=lifespan)


def media_contract(row: sqlite3.Row) -> MediaAsset:
    media_id = row["id"]
    return MediaAsset(
        id=media_id, project_id=row["project_id"], filename=row["filename"],
        display_name=row["display_name"], removed_at=row["removed_at"], duration_ms=row["duration_ms"],
        video_url=f"/api/media/{media_id}/video",
        audio_url=f"/api/media/{media_id}/audio",
        media_offset_ms=row["media_offset_ms"], created_at=row["created_at"],
    )


def find_media(media_id: str) -> sqlite3.Row:
    with connect(DB_PATH) as connection:
        row = connection.execute("SELECT * FROM media_assets WHERE id = ?", (media_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Media not found")
    return row


def checked_media_path(path: str) -> Path:
    try:
        return private_file(path, DATA_DIR)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail="Invalid stored media path") from exc


def ensure_default_tiers(media_id: str) -> None:
    media = find_media(media_id)
    with connect(DB_PATH) as connection:
        connection.executemany(
            "INSERT OR IGNORE INTO annotation_tiers (id, media_id, project_id, name, sort_order) VALUES (?, ?, ?, ?, ?)",
            [(f"{media_id}:{kind}", media_id, media["project_id"], name, index)
             for index, (kind, name) in enumerate(DEFAULT_TIERS)],
        )


def tier_contract(row: sqlite3.Row) -> Tier:
    kind = row["id"].split(":")[-1] if ":" in row["id"] else "custom"
    return Tier(id=row["id"], project_id=row["project_id"], media_id=row["media_id"], name=row["name"],
                kind=kind, sort_order=row["sort_order"])


def annotation_contract(row: sqlite3.Row) -> HumanAnnotation:
    return HumanAnnotation(
        id=row["id"], project_id=row["project_id"], media_id=row["media_id"], tier_id=row["tier_id"],
        span=TimeSpan(start_ms=row["start_ms"], end_ms=row["end_ms"], precision=row["precision"]),
        text=row["text"], event_label=row["event_label"],
        review_status=row["review_status"], source_ids=json.loads(row["source_ids_json"]),
        metadata=json.loads(row["metadata_json"]), created_at=row["created_at"], updated_at=row["updated_at"],
    )


def validate_span(span: TimeSpan, duration_ms: int) -> None:
    if span.end_ms <= span.start_ms or span.end_ms > duration_ms:
        raise HTTPException(status_code=422, detail="Annotation interval must be within media duration")


def find_annotation(media_id: str, annotation_id: str) -> sqlite3.Row:
    with connect(DB_PATH) as connection:
        row = connection.execute(
            "SELECT * FROM human_annotations WHERE id = ? AND media_id = ?",
            (annotation_id, media_id),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Annotation not found")
    return row


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/projects", response_model=list[Project])
def list_projects(include_inactive: bool = False):
    with connect(DB_PATH) as connection:
        where = "" if include_inactive else "WHERE archived_at IS NULL AND deleted_at IS NULL"
        rows = connection.execute(f"SELECT * FROM projects {where} ORDER BY last_opened_at DESC, created_at DESC").fetchall()
    return [project_contract(row) for row in rows]


@app.post("/api/projects", response_model=Project, status_code=201)
def create_project(payload: ProjectCreate):
    return create_project_record(payload.name)


@app.get("/api/projects/{project_id}", response_model=Project)
def get_project(project_id: str):
    return project_contract(find_project(project_id))


@app.post("/api/projects/{project_id}/open", response_model=Project)
def open_project(project_id: str):
    current = find_project(project_id)
    if current["archived_at"] or current["deleted_at"]:
        raise HTTPException(status_code=409, detail="Restore the project before opening it")
    with connect(DB_PATH) as connection:
        connection.execute("UPDATE projects SET last_opened_at = ? WHERE id = ?", (now_utc(), project_id))
        row = connection.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    return project_contract(row)


@app.patch("/api/projects/{project_id}", response_model=Project)
def rename_project(project_id: str, payload: ProjectRename):
    find_project(project_id)
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Project name cannot be blank")
    with connect(DB_PATH) as connection:
        connection.execute("UPDATE projects SET name = ?, updated_at = ? WHERE id = ?", (name, now_utc(), project_id))
        row = connection.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    return project_contract(row)


@app.post("/api/projects/{project_id}/archive", response_model=Project)
def archive_project(project_id: str):
    row = find_project(project_id)
    if row["deleted_at"]:
        raise HTTPException(status_code=409, detail="Deleted project must be restored first")
    with connect(DB_PATH) as connection:
        timestamp = now_utc()
        connection.execute("UPDATE projects SET archived_at = ?, updated_at = ? WHERE id = ?", (timestamp, timestamp, project_id))
        row = connection.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    return project_contract(row)


@app.post("/api/projects/{project_id}/restore", response_model=Project)
def restore_project(project_id: str):
    find_project(project_id)
    with connect(DB_PATH) as connection:
        connection.execute("UPDATE projects SET archived_at = NULL, deleted_at = NULL, updated_at = ? WHERE id = ?", (now_utc(), project_id))
        row = connection.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    return project_contract(row)


@app.post("/api/projects/{project_id}/delete", response_model=Project)
def delete_project(project_id: str, payload: ConfirmDelete):
    row = find_project(project_id)
    if payload.confirm_name != row["name"]:
        raise HTTPException(status_code=422, detail="Type the exact project name to confirm")
    with connect(DB_PATH) as connection:
        timestamp = now_utc()
        connection.execute("UPDATE projects SET deleted_at = ?, updated_at = ? WHERE id = ?", (timestamp, timestamp, project_id))
        row = connection.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    return project_contract(row)


@app.post("/api/projects/{project_id}/open-folder")
def open_project_folder(project_id: str):
    find_project(project_id)
    folder = project_folder(project_id)
    folder.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        raise HTTPException(status_code=501, detail="Open Project Folder is currently supported on Windows")
    try:
        os.startfile(str(folder))  # type: ignore[attr-defined]
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not open project folder: {exc}") from exc
    return {"status": "opened", "storage_path": str(folder)}


@app.get("/api/projects/{project_id}/media", response_model=list[MediaAsset])
def list_project_media(project_id: str, include_removed: bool = False):
    find_project(project_id)
    with connect(DB_PATH) as connection:
        where = "" if include_removed else "AND removed_at IS NULL"
        rows = connection.execute(
            f"SELECT * FROM media_assets WHERE project_id = ? {where} ORDER BY created_at DESC", (project_id,)
        ).fetchall()
    return [media_contract(row) for row in rows]


@app.get("/api/storage/audit")
def storage_audit():
    with connect(DB_PATH) as connection:
        media_rows = connection.execute("SELECT id, original_path, audio_path FROM media_assets").fetchall()
    return {"files": inventory(DATA_DIR, media_rows)}


@app.post("/api/storage/open-folder")
def open_data_folder():
    if os.name != "nt":
        raise HTTPException(status_code=501, detail="Open Data Folder is currently supported on Windows")
    try:
        os.startfile(str(DATA_DIR))  # type: ignore[attr-defined]
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not open data folder: {exc}") from exc
    return {"status": "opened", "storage_path": str(DATA_DIR)}


@app.get("/api/projects/{project_id}/storage")
def project_storage(project_id: str):
    project = find_project(project_id)
    with connect(DB_PATH) as connection:
        media_rows = connection.execute("SELECT * FROM media_assets WHERE project_id = ? ORDER BY created_at", (project_id,)).fetchall()
        tier_rows = connection.execute(
            """SELECT t.id, t.media_id, t.name, COUNT(a.id) AS annotation_count
               FROM annotation_tiers t LEFT JOIN human_annotations a ON a.tier_id = t.id
               WHERE t.project_id = ? GROUP BY t.id ORDER BY t.media_id, t.sort_order""", (project_id,)
        ).fetchall()
        data_counts = {}
        for table in ("source_transcripts", "ocr_observations", "subtitle_screen_states", "derived_events", "vad_intervals"):
            data_counts[table] = connection.execute(
                f"SELECT COUNT(*) FROM {table} WHERE media_id IN (SELECT id FROM media_assets WHERE project_id = ?)",
                (project_id,),
            ).fetchone()[0]
        all_media = connection.execute("SELECT id, original_path, audio_path FROM media_assets").fetchall()
    files = inventory(DATA_DIR, all_media)
    media_ids = {row["id"] for row in media_rows}
    folder_prefix = project["storage_rel_path"].rstrip("/") + "/"
    project_files = [item for item in files if item["relative_path"].startswith(folder_prefix)
                     or media_ids.intersection(item["media_ids"])]
    return {
        "project": project_contract(project).model_dump(),
        "media": [{"id": row["id"], "display_name": row["display_name"],
                   "duration_ms": row["duration_ms"], "created_at": row["created_at"],
                   "removed_at": row["removed_at"], "annotation_count": sum(
                       tier["annotation_count"] for tier in tier_rows if tier["media_id"] == row["id"]),
                   "original_path": row["original_path"], "audio_path": row["audio_path"]}
                  for row in media_rows],
        "tiers": [dict(row) for row in tier_rows],
        "research_counts": data_counts,
        "annotation_count": sum(row["annotation_count"] for row in tier_rows),
        "media_count": len(media_rows),
        "total_storage_bytes": sum(item["size_bytes"] for item in project_files),
        "files": project_files,
    }


@app.get("/api/media", response_model=list[MediaAsset])
def list_media():
    with connect(DB_PATH) as connection:
        rows = connection.execute("SELECT * FROM media_assets ORDER BY created_at DESC").fetchall()
    return [media_contract(row) for row in rows]


@app.get("/api/media/{media_id}", response_model=MediaAsset)
def get_media(media_id: str):
    return media_contract(find_media(media_id))


@app.patch("/api/media/{media_id}", response_model=MediaAsset)
def rename_media(media_id: str, payload: MediaRename):
    media = find_media(media_id)
    name = payload.display_name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Media name cannot be blank")
    with connect(DB_PATH) as connection:
        connection.execute("UPDATE media_assets SET display_name = ? WHERE id = ?", (name, media_id))
        connection.execute("UPDATE projects SET updated_at = ? WHERE id = ?", (now_utc(), media["project_id"]))
        row = connection.execute("SELECT * FROM media_assets WHERE id = ?", (media_id,)).fetchone()
    return media_contract(row)


@app.post("/api/media/{media_id}/remove", response_model=MediaAsset)
def remove_media(media_id: str):
    media = find_media(media_id)
    with connect(DB_PATH) as connection:
        connection.execute("UPDATE media_assets SET removed_at = ? WHERE id = ?", (now_utc(), media_id))
        connection.execute("UPDATE projects SET updated_at = ? WHERE id = ?", (now_utc(), media["project_id"]))
        row = connection.execute("SELECT * FROM media_assets WHERE id = ?", (media_id,)).fetchone()
    return media_contract(row)


@app.post("/api/media/{media_id}/restore", response_model=MediaAsset)
def restore_media(media_id: str):
    media = find_media(media_id)
    with connect(DB_PATH) as connection:
        connection.execute("UPDATE media_assets SET removed_at = NULL WHERE id = ?", (media_id,))
        connection.execute("UPDATE projects SET updated_at = ? WHERE id = ?", (now_utc(), media["project_id"]))
        row = connection.execute("SELECT * FROM media_assets WHERE id = ?", (media_id,)).fetchone()
    return media_contract(row)


@app.post("/api/media", response_model=MediaAsset, status_code=201)
def import_media(file: UploadFile = File(...)):
    return import_media_into_project(compatibility_project_id(), file)


@app.post("/api/projects/{project_id}/media", response_model=MediaAsset, status_code=201)
def import_project_media(project_id: str, file: UploadFile = File(...)):
    return import_media_into_project(project_id, file)


def import_media_into_project(project_id: str, file: UploadFile) -> MediaAsset:
    project = find_project(project_id)
    if project["archived_at"] or project["deleted_at"]:
        raise HTTPException(status_code=409, detail="Restore the project before importing media")
    if Path(file.filename or "").suffix.lower() != ".mp4":
        raise HTTPException(status_code=400, detail="Choose an MP4 file")
    media_id = str(uuid4())
    folder = project_folder(project_id) / "media" / media_id
    folder.mkdir(parents=True, exist_ok=False)
    original = folder / "original.mp4"
    audio = project_folder(project_id) / "derived" / "audio" / f"{media_id}.wav"
    audio.parent.mkdir(parents=True, exist_ok=True)
    try:
        with original.open("wb") as output:
            shutil.copyfileobj(file.file, output)
        duration_ms = probe_duration_ms(original)
        extract_audio(original, audio)
        created_at = now_utc()
        with connect(DB_PATH) as connection:
            connection.execute(
                """INSERT INTO media_assets
                   (id, project_id, filename, display_name, original_path, audio_path, duration_ms, media_offset_ms, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (media_id, project_id, Path(file.filename).name, Path(file.filename).name,
                 str(original), str(audio), duration_ms, 0, created_at),
            )
            connection.execute("UPDATE projects SET updated_at = ? WHERE id = ?", (created_at, project_id))
        if asr_preferences(DB_PATH)["auto_transcribe"]:
            enqueue_asr(DB_PATH, media_id, asr_preferences(DB_PATH)["default_model"])
        return MediaAsset(
            id=media_id, project_id=project_id, filename=Path(file.filename).name,
            display_name=Path(file.filename).name, removed_at=None, duration_ms=duration_ms,
            video_url=f"/api/media/{media_id}/video",
            audio_url=f"/api/media/{media_id}/audio",
            media_offset_ms=0, created_at=created_at,
        )
    except (subprocess.CalledProcessError, KeyError, ValueError) as exc:
        shutil.rmtree(folder, ignore_errors=True)
        audio.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail=f"Could not process MP4: {exc}") from exc


@app.get("/api/media/{media_id}/video")
def video(media_id: str):
    return FileResponse(checked_media_path(find_media(media_id)["original_path"]), media_type="video/mp4")


@app.get("/api/media/{media_id}/audio")
def audio(media_id: str):
    return FileResponse(checked_media_path(find_media(media_id)["audio_path"]), media_type="audio/wav")


def source_words_document(media_id: str) -> dict:
    find_media(media_id)
    with closing(connect(DB_PATH)) as db:
        rows = db.execute("SELECT * FROM source_transcripts WHERE media_id=? ORDER BY start_ms,word_order,id", (media_id,)).fetchall()
        meta = db.execute("SELECT revision FROM source_transcript_meta WHERE media_id=?", (media_id,)).fetchone()
        run = db.execute("SELECT id FROM asr_runs WHERE media_id=? ORDER BY created_at DESC LIMIT 1", (media_id,)).fetchone()
        candidates = db.execute("SELECT * FROM asr_word_candidates WHERE run_id=? ORDER BY word_order", (run[0],)).fetchall() if run else []
    return {
        "revision": meta[0] if meta else 0,
        "words": [{"id": row["id"], "media_id": row["media_id"], "start_ms": row["start_ms"],
                   "end_ms": row["end_ms"], "text": row["text"], "precision": row["precision"],
                   "review_status": row["review_status"], "speech_status": row["speech_status"], "edit_status": row["edit_status"],
                   "attention": json.loads(row["attention_json"]), "asr_run_id": row["asr_run_id"], "asr_candidate_id": row["asr_candidate_id"],
                   "provenance": json.loads(row["provenance_json"])}
                  for row in rows],
        "candidates_needing_attention": [{"id": row["id"], "text": row["text"],
                   "raw_start_seconds": row["raw_start_seconds"], "raw_end_seconds": row["raw_end_seconds"],
                   "issues": json.loads(row["issues_json"])} for row in candidates if json.loads(row["issues_json"])],
    }


@app.get("/api/media/{media_id}/source-words")
def get_source_words(media_id: str):
    return source_words_document(media_id)


@app.put("/api/media/{media_id}/source-words")
def put_source_words(media_id: str, payload: SourceWordsWrite):
    media = find_media(media_id)
    ids = [word.id for word in payload.words]
    if len(ids) != len(set(ids)):
        raise HTTPException(status_code=422, detail="Word IDs must be unique")
    if any(word.end_ms <= word.start_ms or word.end_ms > media["duration_ms"] for word in payload.words):
        raise HTTPException(status_code=422, detail="Words must have valid millisecond intervals within the media duration")
    with closing(connect(DB_PATH)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        meta = db.execute("SELECT revision FROM source_transcript_meta WHERE media_id=?", (media_id,)).fetchone()
        revision = meta[0] if meta else 0
        if revision != payload.expected_revision:
            raise HTTPException(status_code=409, detail="Transcript changed in another session; reload before editing")
        previous = {row["id"]: row for row in db.execute("SELECT * FROM source_transcripts WHERE media_id=?", (media_id,))}
        ordered = sorted(payload.words, key=lambda word: (word.start_ms, word.end_ms, word.id))
        db.execute("DELETE FROM source_transcripts WHERE media_id=?", (media_id,))
        prior_end = None
        now = now_utc()
        for index, word in enumerate(ordered):
            old = previous.get(word.id)
            issues = [] if not old else [issue for issue in json.loads(old["attention_json"]) if issue not in ("overlap_previous", "long_gap_previous")]
            if prior_end is not None:
                if word.start_ms < prior_end: issues.append("overlap_previous")
                if word.start_ms - prior_end > 1000: issues.append("long_gap_previous")
            prior_end = word.end_ms
            changed = not old or any((old["start_ms"] != word.start_ms, old["end_ms"] != word.end_ms,
                                      old["text"] != word.text, old["precision"] != word.precision,
                                      old["speech_status"] != word.speech_status))
            db.execute("""INSERT INTO source_transcripts
               (id,media_id,start_ms,end_ms,precision,text,language,granularity,provenance_json,
                review_status,speech_status,asr_run_id,asr_candidate_id,word_order,edit_status,attention_json,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
               (word.id, media_id, word.start_ms, word.end_ms, word.precision.value, word.text, "en", "word",
                old["provenance_json"] if old else json.dumps({"method": "manual", "model": None, "confidence": None}),
                word.review_status.value, word.speech_status, old["asr_run_id"] if old else None, old["asr_candidate_id"] if old else None, index,
                ("edited" if old else "inserted") if changed else old["edit_status"],
                json.dumps(list(dict.fromkeys(issues))), old["created_at"] if old else now, now))
        db.execute("INSERT INTO source_transcript_meta (media_id,revision,updated_at) VALUES (?,?,?) ON CONFLICT(media_id) DO UPDATE SET revision=excluded.revision,updated_at=excluded.updated_at",
                   (media_id, revision + 1, now))
        db.execute("UPDATE projects SET updated_at=? WHERE id=?", (now, media["project_id"]))
    return source_words_document(media_id)


@app.get("/api/media/{media_id}/source-words/simple-json")
def export_source_words_simple(media_id: str):
    media = find_media(media_id)
    words = [word for word in source_words_document(media_id)["words"]
             if word["speech_status"] != "not_spoken" and word["review_status"] != "rejected"]
    simple = [{"start": round(word["start_ms"] / 1000, 3), "end": round(word["end_ms"] / 1000, 3),
               "text": word["text"]} for word in words]
    filename = f"{safe_name(Path(media['display_name']).stem)}_source_words.json"
    body = json.dumps(simple, ensure_ascii=False, indent=2).encode("utf-8")
    return Response(content=body, media_type="application/json; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename=source_words.json; filename*=UTF-8''{quote(filename)}"})


@app.get("/api/media/{media_id}/alignment/diagnostics")
def alignment_diagnostics(media_id: str):
    media = find_media(media_id)
    with closing(connect(DB_PATH)) as db:
        return timing_diagnostics(db, media)


@app.post("/api/media/{media_id}/alignment/preview")
def preview_alignment(media_id: str, payload: AlignmentPreviewRequest):
    media = find_media(media_id)
    if payload.end_ms > media["duration_ms"] or payload.end_ms <= payload.start_ms:
        raise HTTPException(422, "Select a valid range within the media")
    with closing(connect(DB_PATH)) as db:
        meta = db.execute("SELECT revision FROM source_transcript_meta WHERE media_id=?", (media_id,)).fetchone()
        if (meta[0] if meta else 0) != payload.expected_revision:
            raise HTTPException(409, "Transcript changed; reload before alignment")
        rows = db.execute("""SELECT id,text,start_ms,end_ms,review_status FROM source_transcripts
            WHERE media_id=? AND start_ms>=? AND end_ms<=? ORDER BY start_ms,word_order,id""",
            (media_id, payload.start_ms, payload.end_ms)).fetchall()
        words = [dict(row) for row in rows]
    try:
        changes = StableTsWordAligner().preview(checked_media_path(media["audio_path"]), words,
            payload.start_ms, payload.end_ms, payload.model, DATA_DIR / "models" / "stable-ts")
    except (ValueError, RuntimeError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc
    preview_id, now = str(uuid4()), now_utc()
    proposal = [change.__dict__ | {"review_status": word["review_status"]} for change, word in zip(changes, words)]
    with closing(connect(DB_PATH)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        meta = db.execute("SELECT revision FROM source_transcript_meta WHERE media_id=?", (media_id,)).fetchone()
        if (meta[0] if meta else 0) != payload.expected_revision:
            raise HTTPException(409, "Transcript changed while alignment was running")
        db.execute("INSERT INTO alignment_previews (id,media_id,revision,start_ms,end_ms,engine,model,changes_json,status,created_at) VALUES (?,?,?,?,?,'stable-ts',?,?,'pending',?)",
            (preview_id, media_id, payload.expected_revision, payload.start_ms, payload.end_ms, payload.model,
             json.dumps(proposal, ensure_ascii=False), now))
    return {"id": preview_id, "status": "pending", "engine": "stable-ts", "model": payload.model, "changes": proposal,
            "reviewed_word_count": sum(word["review_status"] == "reviewed" for word in words)}


@app.post("/api/media/{media_id}/alignment/previews/{preview_id}/reject")
def reject_alignment(media_id: str, preview_id: str):
    find_media(media_id)
    with closing(connect(DB_PATH)) as db, db:
        changed = db.execute("UPDATE alignment_previews SET status='rejected',resolved_at=? WHERE id=? AND media_id=? AND status='pending'",
                             (now_utc(), preview_id, media_id)).rowcount
    if not changed: raise HTTPException(404, "Pending preview not found")
    return {"status": "rejected"}


@app.post("/api/media/{media_id}/alignment/previews/{preview_id}/apply")
def apply_alignment(media_id: str, preview_id: str, payload: AlignmentApplyRequest):
    find_media(media_id)
    with closing(connect(DB_PATH)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        preview = db.execute("SELECT * FROM alignment_previews WHERE id=? AND media_id=? AND status='pending'", (preview_id, media_id)).fetchone()
        if not preview: raise HTTPException(404, "Pending preview not found")
        meta = db.execute("SELECT revision FROM source_transcript_meta WHERE media_id=?", (media_id,)).fetchone()
        if (meta[0] if meta else 0) != preview["revision"]:
            raise HTTPException(409, "Transcript changed since preview; create a new preview")
        changes = json.loads(preview["changes_json"])
        if any(change["review_status"] == "reviewed" for change in changes) and not payload.confirm_reviewed:
            raise HTTPException(409, "Reviewed words require explicit confirmation")
        for change in changes:
            row = db.execute("SELECT * FROM source_transcripts WHERE id=? AND media_id=?", (change["word_id"], media_id)).fetchone()
            if not row or (row["text"], row["start_ms"], row["end_ms"], row["review_status"]) != (
                change["text"], change["old_start_ms"], change["old_end_ms"], change["review_status"]):
                raise HTTPException(409, "Selected word changed since preview")
        now = now_utc()
        for change in changes:
            db.execute("UPDATE source_transcripts SET start_ms=?,end_ms=?,edit_status='edited',updated_at=? WHERE id=?",
                (change["proposed_start_ms"], change["proposed_end_ms"], now, change["word_id"]))
        db.execute("UPDATE alignment_previews SET status='applied',resolved_at=? WHERE id=?", (now, preview_id))
        db.execute("UPDATE source_transcript_meta SET revision=revision+1,updated_at=? WHERE media_id=?", (now, media_id))
    return source_words_document(media_id)


@app.get("/api/asr/settings")
def get_asr_settings():
    return asr_preferences(DB_PATH)


@app.put("/api/asr/settings")
def put_asr_settings(payload: ASRSettingsWrite):
    value = payload.model_dump()
    with closing(connect(DB_PATH)) as db, db:
        db.execute("INSERT INTO app_preferences (key,value_json) VALUES ('asr',?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
                   (json.dumps(value),))
    return value


@app.get("/api/asr/capabilities")
def asr_capabilities():
    from importlib.util import find_spec
    return {"engine": "stable-ts", "installed": find_spec("stable_whisper") is not None,
            "device": detect_device(), "models": MODELS}


@app.get("/api/media/{media_id}/asr")
def asr_status(media_id: str):
    find_media(media_id)
    with closing(connect(DB_PATH)) as db:
        job = db.execute("SELECT * FROM asr_jobs WHERE media_id=? ORDER BY requested_at DESC LIMIT 1", (media_id,)).fetchone()
        count = db.execute("SELECT COUNT(*) FROM source_transcripts WHERE media_id=?", (media_id,)).fetchone()[0]
    return {"job": dict(job) if job else None, "word_count": count}


@app.post("/api/media/{media_id}/asr/jobs", status_code=201)
def start_asr_job(media_id: str, payload: ASRJobCreate):
    find_media(media_id)
    try:
        job = enqueue_asr(DB_PATH, media_id, payload.model or asr_preferences(DB_PATH)["default_model"], rerun=payload.rerun)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return dict(job)


@app.post("/api/media/{media_id}/asr/jobs/{job_id}/cancel")
def stop_asr_job(media_id: str, job_id: str):
    find_media(media_id)
    with closing(connect(DB_PATH)) as db:
        owned = db.execute("SELECT 1 FROM asr_jobs WHERE id=? AND media_id=?", (job_id, media_id)).fetchone()
    if not owned:
        raise HTTPException(status_code=404, detail="ASR job not found")
    job = cancel_asr_job(DB_PATH, job_id)
    return dict(job)


@app.get("/api/media/{media_id}/vad")
def vad_status(media_id: str):
    find_media(media_id)
    with closing(connect(DB_PATH)) as db:
        job = db.execute("SELECT * FROM vad_jobs WHERE media_id=? ORDER BY requested_at DESC LIMIT 1", (media_id,)).fetchone()
        run = db.execute("SELECT * FROM vad_runs WHERE media_id=? ORDER BY created_at DESC LIMIT 1", (media_id,)).fetchone()
        intervals = db.execute("SELECT * FROM vad_intervals WHERE run_id=? ORDER BY start_ms", (run["id"],)).fetchall() if run else []
    return {"job": dict(job) if job else None,
            "run": {**dict(run), "parameters": json.loads(run["parameters_json"])} if run else None,
            "intervals": [dict(row) for row in intervals]}


@app.post("/api/media/{media_id}/vad/jobs", status_code=201)
def start_vad_job(media_id: str):
    find_media(media_id)
    try: return dict(enqueue_vad(DB_PATH, media_id))
    except ValueError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/media/{media_id}/vad/jobs/{job_id}/cancel")
def stop_vad_job(media_id: str, job_id: str):
    find_media(media_id)
    with closing(connect(DB_PATH)) as db:
        owned = db.execute("SELECT 1 FROM vad_jobs WHERE id=? AND media_id=?", (job_id, media_id)).fetchone()
    if not owned: raise HTTPException(status_code=404, detail="VAD job not found")
    return dict(cancel_vad_job(DB_PATH, job_id))


@app.get("/api/media/{media_id}/target-ocr")
def target_ocr_status(media_id: str):
    find_media(media_id)
    with closing(connect(DB_PATH)) as db:
        job = db.execute("SELECT * FROM ocr_jobs WHERE media_id=? ORDER BY requested_at DESC,id DESC LIMIT 1", (media_id,)).fetchone()
        run = db.execute("SELECT * FROM ocr_runs WHERE media_id=? ORDER BY created_at DESC,id DESC LIMIT 1", (media_id,)).fetchone()
        if run:
            states = db.execute("SELECT * FROM subtitle_screen_states WHERE media_id=? AND run_id=? ORDER BY start_ms,id",
                                (media_id, run["id"])).fetchall()
        else:
            states = db.execute("SELECT * FROM subtitle_screen_states WHERE media_id=? AND run_id IS NULL ORDER BY start_ms,id",
                                (media_id,)).fetchall()
    return {"job": {**dict(job), "parameters": json.loads(job["parameters_json"])} if job else None,
            "run": {**dict(run), "parameters": json.loads(run["parameters_json"])} if run else None,
            "states": [{**dict(row), "observation_ids": json.loads(row["observation_ids_json"]),
                        "provenance": json.loads(row["provenance_json"])} for row in states]}


@app.post("/api/media/{media_id}/target-ocr/jobs", status_code=201)
def start_target_ocr(media_id: str, payload: OCRJobCreate):
    media = find_media(media_id)
    params = payload.model_dump()
    params["end_ms"] = payload.end_ms if payload.end_ms is not None else min(media["duration_ms"], payload.start_ms + 30000)
    try:
        validate_roi(params["roi"])
        return dict(enqueue_ocr(DB_PATH, media_id, params))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/media/{media_id}/target-ocr/jobs/{job_id}/cancel")
def stop_target_ocr(media_id: str, job_id: str):
    find_media(media_id)
    with closing(connect(DB_PATH)) as db:
        owned = db.execute("SELECT 1 FROM ocr_jobs WHERE id=? AND media_id=?", (job_id, media_id)).fetchone()
    if not owned: raise HTTPException(status_code=404, detail="OCR job not found")
    return dict(cancel_ocr_job(DB_PATH, job_id))


@app.put("/api/media/{media_id}/target-ocr/states/{state_id}")
def update_subtitle_state(media_id: str, state_id: str, payload: SubtitleStateWrite):
    media = find_media(media_id)
    if payload.end_ms <= payload.start_ms or payload.end_ms > media["duration_ms"]:
        raise HTTPException(status_code=422, detail="State interval must fit the media")
    with closing(connect(DB_PATH)) as db, db:
        state = db.execute("SELECT * FROM subtitle_screen_states WHERE id=? AND media_id=?", (state_id, media_id)).fetchone()
        if not state: raise HTTPException(status_code=404, detail="Subtitle state not found")
        db.execute("UPDATE subtitle_screen_states SET start_ms=?,end_ms=?,text=?,review_status=? WHERE id=? AND media_id=?",
                   (payload.start_ms, payload.end_ms, payload.text, payload.review_status.value, state_id, media_id))
        updated = db.execute("SELECT * FROM subtitle_screen_states WHERE id=?", (state_id,)).fetchone()
    return {**dict(updated), "observation_ids": json.loads(updated["observation_ids_json"]),
            "provenance": json.loads(updated["provenance_json"])}


@app.get("/api/media/{media_id}/tiers", response_model=list[Tier])
def list_tiers(media_id: str):
    ensure_default_tiers(media_id)
    with connect(DB_PATH) as connection:
        rows = connection.execute(
            "SELECT * FROM annotation_tiers WHERE media_id = ? ORDER BY sort_order, name",
            (media_id,),
        ).fetchall()
    return [tier_contract(row) for row in rows]


@app.post("/api/media/{media_id}/tiers", response_model=Tier, status_code=201)
def create_tier(media_id: str, payload: TierCreate):
    ensure_default_tiers(media_id)
    media = find_media(media_id)
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Tier name cannot be blank")
    tier_id = str(uuid4())
    with connect(DB_PATH) as connection:
        order = connection.execute(
            "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM annotation_tiers WHERE media_id = ?",
            (media_id,),
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO annotation_tiers (id, media_id, project_id, name, sort_order) VALUES (?, ?, ?, ?, ?)",
            (tier_id, media_id, media["project_id"], name, order),
        )
        connection.execute("UPDATE projects SET updated_at = ? WHERE id = ?", (now_utc(), media["project_id"]))
    return Tier(id=tier_id, project_id=media["project_id"], media_id=media_id, name=name, kind="custom", sort_order=order)


@app.put("/api/media/{media_id}/tiers/order", response_model=list[Tier])
def reorder_tiers(media_id: str, payload: TierOrder):
    existing = list_tiers(media_id)
    if len(payload.ids) != len(existing) or set(payload.ids) != {tier.id for tier in existing}:
        raise HTTPException(status_code=422, detail="Provide every tier ID exactly once")
    with closing(connect(DB_PATH)) as db, db:
        db.executemany("UPDATE annotation_tiers SET sort_order=? WHERE media_id=? AND id=?",
                       [(index, media_id, tier_id) for index, tier_id in enumerate(payload.ids)])
    return list_tiers(media_id)


@app.put("/api/media/{media_id}/tiers/{tier_id}", response_model=Tier)
def rename_tier(media_id: str, tier_id: str, payload: TierCreate):
    name = payload.name.strip()
    if not name: raise HTTPException(status_code=422, detail="Tier name cannot be blank")
    with closing(connect(DB_PATH)) as db, db:
        row = db.execute("SELECT * FROM annotation_tiers WHERE id=? AND media_id=?", (tier_id, media_id)).fetchone()
        if not row: raise HTTPException(status_code=404, detail="Tier not found")
        db.execute("UPDATE annotation_tiers SET name=? WHERE id=?", (name, tier_id))
        changed = db.execute("SELECT * FROM annotation_tiers WHERE id=?", (tier_id,)).fetchone()
    return tier_contract(changed)


@app.delete("/api/media/{media_id}/tiers/{tier_id}", status_code=204)
def delete_tier(media_id: str, tier_id: str, confirm_name: str):
    with closing(connect(DB_PATH)) as db, db:
        row = db.execute("SELECT * FROM annotation_tiers WHERE id=? AND media_id=?", (tier_id, media_id)).fetchone()
        if not row: raise HTTPException(status_code=404, detail="Tier not found")
        if ":" in tier_id: raise HTTPException(status_code=409, detail="Built-in tiers cannot be deleted")
        if confirm_name != row["name"]: raise HTTPException(status_code=422, detail="Confirm the exact tier name")
        if db.execute("SELECT 1 FROM human_annotations WHERE tier_id=? LIMIT 1", (tier_id,)).fetchone():
            raise HTTPException(status_code=409, detail="Tier contains annotations; move or delete them first")
        db.execute("DELETE FROM annotation_tiers WHERE id=? AND media_id=?", (tier_id, media_id))


@app.get("/api/media/{media_id}/annotations", response_model=list[HumanAnnotation])
def list_annotations(media_id: str):
    find_media(media_id)
    with connect(DB_PATH) as connection:
        rows = connection.execute(
            "SELECT * FROM human_annotations WHERE media_id = ? ORDER BY start_ms, id",
            (media_id,),
        ).fetchall()
    return [annotation_contract(row) for row in rows]


@app.post("/api/media/{media_id}/annotations", response_model=HumanAnnotation, status_code=201)
def create_annotation(media_id: str, payload: AnnotationWrite):
    media = find_media(media_id)
    ensure_default_tiers(media_id)
    validate_span(payload.span, media["duration_ms"])
    with connect(DB_PATH) as connection:
        tier = connection.execute(
            "SELECT id FROM annotation_tiers WHERE id = ? AND media_id = ?",
            (payload.tier_id, media_id),
        ).fetchone()
        if tier is None:
            raise HTTPException(status_code=422, detail="Tier does not belong to media")
        annotation_id, timestamp = str(uuid4()), now_utc()
        connection.execute(
            """INSERT INTO human_annotations
               (id, project_id, media_id, tier_id, start_ms, end_ms, precision, text,
                event_label, review_status, metadata_json, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (annotation_id, media["project_id"], media_id, payload.tier_id, payload.span.start_ms,
             payload.span.end_ms, payload.span.precision.value, payload.text,
             payload.event_label, payload.review_status.value,
             json.dumps(payload.metadata, ensure_ascii=False), timestamp, timestamp),
        )
        connection.execute("UPDATE projects SET updated_at = ? WHERE id = ?", (timestamp, media["project_id"]))
        row = connection.execute("SELECT * FROM human_annotations WHERE id = ?", (annotation_id,)).fetchone()
    return annotation_contract(row)


@app.put("/api/media/{media_id}/annotations/{annotation_id}", response_model=HumanAnnotation)
def update_annotation(media_id: str, annotation_id: str, payload: AnnotationUpdate):
    media = find_media(media_id)
    existing = find_annotation(media_id, annotation_id)
    validate_span(payload.span, media["duration_ms"])
    metadata_json = existing["metadata_json"] if payload.metadata is None else json.dumps(payload.metadata, ensure_ascii=False)
    with connect(DB_PATH) as connection:
        connection.execute(
            """UPDATE human_annotations SET start_ms = ?, end_ms = ?, precision = ?,
               text = ?, event_label = ?, review_status = ?, metadata_json = ?, updated_at = ?
               WHERE id = ? AND media_id = ?""",
            (payload.span.start_ms, payload.span.end_ms, payload.span.precision.value,
             payload.text, payload.event_label, payload.review_status.value, metadata_json,
             now_utc(), annotation_id, media_id),
        )
        connection.execute("UPDATE projects SET updated_at = ? WHERE id = ?", (now_utc(), media["project_id"]))
        row = connection.execute("SELECT * FROM human_annotations WHERE id = ?", (annotation_id,)).fetchone()
    return annotation_contract(row)


@app.delete("/api/media/{media_id}/annotations/{annotation_id}", status_code=204)
def delete_annotation(media_id: str, annotation_id: str):
    find_annotation(media_id, annotation_id)
    media = find_media(media_id)
    with connect(DB_PATH) as connection:
        connection.execute(
            "DELETE FROM human_annotations WHERE id = ? AND media_id = ?", (annotation_id, media_id),
        )
        connection.execute("UPDATE projects SET updated_at = ? WHERE id = ?", (now_utc(), media["project_id"]))


CSV_COLUMNS = [
    "annotation_id", "project_id", "media_id", "tier_id", "tier_name",
    "start_ms", "end_ms", "duration_ms", "text", "label", "metadata",
    "created_at", "updated_at",
]


def export_data(project_id: str, media_id: str | None = None) -> dict:
    project = find_project(project_id)
    with connect(DB_PATH) as connection:
        media_rows = connection.execute(
            "SELECT * FROM media_assets WHERE project_id = ? ORDER BY created_at, id", (project_id,)
        ).fetchall()
        if media_id is not None:
            media_rows = [row for row in media_rows if row["id"] == media_id]
            if not media_rows:
                raise HTTPException(status_code=404, detail="Media not found in project")
        media_ids = [row["id"] for row in media_rows]
        if media_ids:
            marks = ",".join("?" for _ in media_ids)
            tier_rows = connection.execute(
                f"SELECT * FROM annotation_tiers WHERE media_id IN ({marks}) ORDER BY media_id, sort_order, id", media_ids
            ).fetchall()
            annotation_rows = connection.execute(
                f"SELECT * FROM human_annotations WHERE media_id IN ({marks}) ORDER BY media_id, start_ms, id", media_ids
            ).fetchall()
            raw_tables = {}
            latest_vad = {row["media_id"]: row["id"] for row in connection.execute(
                f"SELECT id,media_id FROM vad_runs WHERE media_id IN ({marks}) ORDER BY created_at,id", media_ids)}
            for table in ("source_transcripts", "ocr_observations", "subtitle_screen_states", "derived_events", "vad_intervals"):
                rows = connection.execute(
                    f"SELECT * FROM {table} WHERE media_id IN ({marks}) ORDER BY media_id, id", media_ids
                ).fetchall()
                if table == "vad_intervals":
                    rows = [row for row in rows if row["run_id"] == latest_vad.get(row["media_id"])]
                raw_tables[table] = [
                    {key[:-5] if key.endswith("_json") else key:
                     json.loads(row[key]) if key.endswith("_json") else row[key]
                     for key in row.keys()} for row in rows
                ]
        else:
            tier_rows, annotation_rows = [], []
            raw_tables = {table: [] for table in
                          ("source_transcripts", "ocr_observations", "subtitle_screen_states", "derived_events", "vad_intervals")}
    return {
        "schema_version": 1, "exported_at": now_utc(),
        "scope": "media" if media_id else "project",
        "project": {key: project[key] for key in
                    ("id", "name", "created_at", "updated_at", "last_opened_at")}
                   | {"settings": json.loads(project["settings_json"])},
        "media_assets": [media_contract(row).model_dump() for row in media_rows],
        "annotation_tiers": [tier_contract(row).model_dump() for row in tier_rows],
        "annotations": [annotation_contract(row).model_dump(mode="json") for row in annotation_rows],
        **raw_tables,
    }


def csv_table(rows: list[dict], columns: list[str]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value
                         for key, value in row.items()})
    return output.getvalue().encode("utf-8-sig")


def annotation_csv_rows(data: dict) -> list[dict]:
    tier_names = {tier["id"]: tier["name"] for tier in data["annotation_tiers"]}
    return [{
        "annotation_id": item["id"], "project_id": item["project_id"],
        "media_id": item["media_id"], "tier_id": item["tier_id"],
        "tier_name": tier_names.get(item["tier_id"], ""),
        "start_ms": item["span"]["start_ms"], "end_ms": item["span"]["end_ms"],
        "duration_ms": item["span"]["end_ms"] - item["span"]["start_ms"],
        "text": item["text"], "label": item["event_label"],
        "metadata": item["metadata"], "created_at": item["created_at"],
        "updated_at": item["updated_at"],
    } for item in data["annotations"]]


@app.get("/api/projects/{project_id}/export")
def download_export(project_id: str, format: Literal["json", "csv"], media_id: str | None = None):
    data = redact_private_values(export_data(project_id, media_id))
    scope = f"media-{media_id}" if media_id else f"project-{project_id}"
    if format == "json":
        payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        return Response(payload, media_type="application/json; charset=utf-8", headers={
            "Content-Disposition": f'attachment; filename="{scope}.json"',
        })
    annotations = csv_table(annotation_csv_rows(data), CSV_COLUMNS)
    if media_id:
        return Response(annotations, media_type="text/csv; charset=utf-8", headers={
            "Content-Disposition": f'attachment; filename="{scope}.csv"',
        })
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("annotations.csv", annotations)
        archive.writestr("media.csv", csv_table(data["media_assets"],
                                                  ["id", "project_id", "filename", "duration_ms", "media_offset_ms", "created_at", "video_url", "audio_url"]))
        archive.writestr("tiers.csv", csv_table(data["annotation_tiers"],
                                                  ["id", "project_id", "media_id", "name", "kind", "sort_order"]))
        archive.writestr("project.json", json.dumps(data["project"], ensure_ascii=False, indent=2).encode("utf-8"))
        for table in ("source_transcripts", "ocr_observations", "subtitle_screen_states", "derived_events", "vad_intervals"):
            records = data[table]
            archive.writestr(f"{table}.csv", csv_table(records, list(records[0]) if records else ["id", "media_id"]))
    return Response(output.getvalue(), media_type="application/zip", headers={
        "Content-Disposition": f'attachment; filename="{scope}.zip"',
    })


@app.get("/api/projects/{project_id}/export/options")
def export_options(project_id: str, media_id: str | None = None):
    data = export_data(project_id, media_id)
    counts: dict[str, int] = {}
    for record in data["annotations"]:
        counts[record["tier_id"]] = counts.get(record["tier_id"], 0) + 1
    return {"scope": data["scope"], "media_assets": data["media_assets"],
            "tiers": [{**tier, "record_count": counts.get(tier["id"], 0)} for tier in data["annotation_tiers"]],
            "research_data": [{"name": key, "record_count": len(data[key])} for key in RESEARCH_TYPES]}


def prepared_simple_export(project_id: str, payload: SimpleExportRequest):
    data = redact_private_values(export_data(project_id, payload.media_id))
    if not payload.complete_project and payload.format != "detailed_json" and "subtitle_screen_states" in payload.datasets:
        with closing(connect(DB_PATH)) as db:
            latest = {row["media_id"]: row["id"] for row in db.execute(
                "SELECT id,media_id FROM ocr_runs ORDER BY created_at,id")}
        data["subtitle_screen_states"] = [row for row in data["subtitle_screen_states"]
            if row.get("run_id") == latest.get(row["media_id"])]
    available = set(SIMPLE_DATASETS) | {f"tier:{tier['id']}" for tier in data["annotation_tiers"]}
    if not payload.complete_project and (not payload.datasets or len(set(payload.datasets)) != len(payload.datasets) or not set(payload.datasets) <= available):
        raise HTTPException(422, "Select one or more available datasets")
    if payload.complete_project and payload.media_id is not None:
        raise HTTPException(422, "Complete project archive requires entire project scope")
    if payload.complete_project:
        media_ids = [item["id"] for item in data["media_assets"]]
        with closing(connect(DB_PATH)) as db:
            for table, key in (("asr_jobs", "media_id"), ("asr_runs", "media_id"),
                               ("source_transcript_meta", "media_id"), ("alignment_previews", "media_id"),
                               ("vad_jobs", "media_id"), ("vad_runs", "media_id"), ("all_vad_intervals", "media_id"),
                               ("ocr_jobs", "media_id"), ("ocr_runs", "media_id")):
                actual_table = "vad_intervals" if table == "all_vad_intervals" else table
                rows = db.execute(f"SELECT * FROM {actual_table} WHERE {key} IN ({','.join('?' for _ in media_ids)})", media_ids).fetchall() if media_ids else []
                data[table] = [{key[:-5] if key.endswith("_json") else key:
                                json.loads(row[key]) if key.endswith("_json") else row[key]
                                for key in row.keys()} for row in rows]
            run_ids = [item["id"] for item in data["asr_runs"]]
            rows = db.execute(f"SELECT * FROM asr_word_candidates WHERE run_id IN ({','.join('?' for _ in run_ids)})", run_ids).fetchall() if run_ids else []
            data["asr_word_candidates"] = [{key[:-5] if key.endswith("_json") else key:
                                             json.loads(row[key]) if key.endswith("_json") else row[key]
                                             for key in row.keys()} for row in rows]
        data = redact_private_values(data)
    return build_files(data, payload.datasets, payload.format, payload.complete_project)


@app.post("/api/projects/{project_id}/export/simple/preview")
def preview_simple_export(project_id: str, payload: SimpleExportRequest):
    filename, media_type, content, files = prepared_simple_export(project_id, payload)
    return {"filename": filename, "media_type": media_type, "bytes": len(content),
            "files": files, "record_count": sum(item["count"] for item in files)}


@app.post("/api/projects/{project_id}/export/simple/download")
def download_simple_export(project_id: str, payload: SimpleExportRequest):
    filename, media_type, content, _ = prepared_simple_export(project_id, payload)
    return Response(content, media_type=f"{media_type}; charset=utf-8" if media_type != "application/zip" else media_type,
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"})


@app.post("/api/projects/{project_id}/export/advanced")
def download_advanced_export(project_id: str, payload: AdvancedExportRequest):
    data = export_data(project_id, payload.media_id)
    available = {tier["id"] for tier in data["annotation_tiers"]}
    if not set(payload.tier_ids) <= available:
        raise HTTPException(status_code=422, detail="Selected tier does not belong to this export scope")
    if not payload.tier_ids and not payload.research_data:
        raise HTTPException(status_code=422, detail="Select at least one tier or research-data collection")
    try:
        body, media_type, filename = build_advanced_export(
            data, payload.tier_ids, payload.research_data,
            payload.format, payload.structure, payload.include_empty_tiers,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    extension = filename.rsplit(".", 1)[-1]
    disposition = f'attachment; filename="annotations.{extension}"; filename*=UTF-8\'\'{quote(filename)}'
    return Response(body, media_type=media_type, headers={"Content-Disposition": disposition})


