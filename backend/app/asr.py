"""Local word-level ASR, normalization, and durable SQLite job processing."""
import json
import math
import sqlite3
import threading
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol
from dataclasses import dataclass
from uuid import uuid4

from .db import connect

MODELS = ("small", "medium")


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def detect_device() -> str:
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"
    except (ImportError, OSError, RuntimeError):
        return "cpu"


class TranscriptionEngine(Protocol):
    def transcribe(self, audio_path: Path, model: str, cache_dir: Path) -> tuple[dict, str]: ...


@dataclass(frozen=True)
class AlignmentChange:
    """A future preview keeps both old and proposed times for explicit review."""
    word_id: str
    text: str
    old_start_ms: int
    old_end_ms: int
    proposed_start_ms: int
    proposed_end_ms: int


class RealignmentEngine(Protocol):
    def preview(self, audio_path: Path, words: list[dict], start_ms: int, end_ms: int) -> list[AlignmentChange]: ...


class StableTsEngine:
    def transcribe(self, audio_path: Path, model: str, cache_dir: Path) -> tuple[dict, str]:
        try:
            import stable_whisper
        except ImportError as exc:
            raise RuntimeError("stable-ts is not installed. Install backend/requirements-asr.txt, then retry.") from exc
        cache_dir.mkdir(parents=True, exist_ok=True)
        device = detect_device()
        def run(target_device: str) -> dict:
            whisper_model = stable_whisper.load_model(model, device=target_device, download_root=str(cache_dir))
            result = whisper_model.transcribe(
                str(audio_path), language="en", word_timestamps=True, verbose=None,
                vad=False, regroup=False,
            )
            return result.to_dict()
        try:
            raw = run(device)
        except (RuntimeError, OSError):
            if device != "cuda":
                raise
            device = "cpu"
            raw = run(device)
        return raw, device


def extract_words(raw: dict, duration_ms: int) -> list[dict]:
    """Keep every raw word; only trustworthy aligned words become timed tokens."""
    words: list[dict] = []
    previous_end: int | None = None
    for segment in raw.get("segments", []):
        for word in segment.get("words", []):
            start, end = word.get("start"), word.get("end")
            probability = word.get("probability")
            confidence = probability if isinstance(probability, (int, float)) and math.isfinite(probability) else None
            raw_text = str(word.get("word", word.get("text", "")))
            text = raw_text.strip()
            issues: list[str] = []
            if not text.strip(): issues.append("empty_text")
            if not isinstance(start, (int, float)) or not isinstance(end, (int, float)) or not math.isfinite(start) or not math.isfinite(end):
                issues.append("missing_or_invalid_timestamp")
                start_ms = end_ms = None
            else:
                start_ms, end_ms = round(start * 1000), round(end * 1000)
                if start_ms < 0 or end_ms > duration_ms or end_ms <= start_ms:
                    issues.append("out_of_range_or_zero_duration")
                if previous_end is not None:
                    if start_ms < previous_end: issues.append("overlap_previous")
                    if start_ms - previous_end > 1000: issues.append("long_gap_previous")
                previous_end = end_ms
            words.append({"order": len(words), "text": text, "raw_text": raw_text,
                          "raw_start": start, "raw_end": end, "confidence": confidence,
                          "start_ms": start_ms, "end_ms": end_ms, "issues": issues,
                          "timed": start_ms is not None and end_ms is not None and
                          start_ms >= 0 and end_ms <= duration_ms and end_ms > start_ms and bool(text.strip())})
    return words


def preferences(db_path: Path) -> dict:
    with closing(connect(db_path)) as db:
        row = db.execute("SELECT value_json FROM app_preferences WHERE key = 'asr'").fetchone()
    return json.loads(row[0]) if row else {"auto_transcribe": True, "default_model": "small"}


def enqueue(db_path: Path, media_id: str, model: str, *, rerun: bool = False) -> sqlite3.Row:
    if model not in MODELS: raise ValueError("Choose small or medium")
    with closing(connect(db_path)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        media = db.execute("SELECT id FROM media_assets WHERE id = ?", (media_id,)).fetchone()
        if not media: raise ValueError("Media not found")
        active = db.execute("SELECT * FROM asr_jobs WHERE media_id = ? AND status IN ('queued','running')", (media_id,)).fetchone()
        if active: return active
        existing = db.execute("SELECT 1 FROM source_transcripts WHERE media_id = ? LIMIT 1", (media_id,)).fetchone() or db.execute(
            "SELECT 1 FROM source_transcript_meta WHERE media_id = ? LIMIT 1", (media_id,)).fetchone()
        if existing and not rerun: raise ValueError("Source transcript already exists. Choose Re-transcribe deliberately.")
        job_id = str(uuid4())
        db.execute("INSERT INTO asr_jobs (id,media_id,model,status,requested_at) VALUES (?,?,?,'queued',?)",
                   (job_id, media_id, model, timestamp()))
        return db.execute("SELECT * FROM asr_jobs WHERE id = ?", (job_id,)).fetchone()


def cancel(db_path: Path, job_id: str) -> sqlite3.Row | None:
    with closing(connect(db_path)) as db, db:
        row = db.execute("SELECT * FROM asr_jobs WHERE id = ?", (job_id,)).fetchone()
        if not row: return None
        if row["status"] == "queued":
            db.execute("UPDATE asr_jobs SET status='cancelled',cancel_requested=1,finished_at=? WHERE id=?", (timestamp(), job_id))
        elif row["status"] == "running":
            db.execute("UPDATE asr_jobs SET cancel_requested=1 WHERE id=?", (job_id,))
        return db.execute("SELECT * FROM asr_jobs WHERE id = ?", (job_id,)).fetchone()


def recover_interrupted(db_path: Path) -> None:
    with closing(connect(db_path)) as db, db:
        db.execute("UPDATE asr_jobs SET status='failed', error='Interrupted by application restart; retry to run again.', finished_at=? WHERE status='running'",
                   (timestamp(),))


def process_next_job(db_path: Path, data_dir: Path, engine: TranscriptionEngine | None = None) -> bool:
    with closing(connect(db_path)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        job = db.execute("SELECT * FROM asr_jobs WHERE status='queued' ORDER BY requested_at LIMIT 1").fetchone()
        if not job: return False
        db.execute("UPDATE asr_jobs SET status='running',started_at=? WHERE id=?", (timestamp(), job["id"]))
        job = dict(job)
    try:
        with closing(connect(db_path)) as db:
            media = db.execute("SELECT audio_path,duration_ms,project_id FROM media_assets WHERE id=?", (job["media_id"],)).fetchone()
        if media is None: raise RuntimeError("Media record is missing")
        audio_path = Path(media["audio_path"]).resolve()
        if data_dir.resolve() not in audio_path.parents or not audio_path.is_file():
            raise RuntimeError("Extracted audio is unavailable in private storage")
        raw, device = (engine or StableTsEngine()).transcribe(audio_path, job["model"], data_dir / "models" / "stable-ts")
        words = extract_words(raw, media["duration_ms"])
        with closing(connect(db_path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT cancel_requested FROM asr_jobs WHERE id=?", (job["id"],)).fetchone()
            if current["cancel_requested"]:
                db.execute("UPDATE asr_jobs SET status='cancelled',finished_at=? WHERE id=?", (timestamp(), job["id"]))
                return True
            run_id, now = str(uuid4()), timestamp()
            db.execute("INSERT INTO asr_runs (id,job_id,media_id,model,device,raw_json,created_at) VALUES (?,?,?,?,?,?,?)",
                       (run_id, job["id"], job["media_id"], job["model"], device, json.dumps(raw, ensure_ascii=False), now))
            candidate_ids = {}
            for word in words:
                candidate_id = str(uuid4())
                candidate_ids[word["order"]] = candidate_id
                db.execute("""INSERT INTO asr_word_candidates
                   (id,run_id,word_order,raw_start_seconds,raw_end_seconds,text,issues_json)
                   VALUES (?,?,?,?,?,?,?)""", (candidate_id, run_id, word["order"],
                   word["raw_start"] if isinstance(word["raw_start"], (int, float)) and math.isfinite(word["raw_start"]) else None,
                   word["raw_end"] if isinstance(word["raw_end"], (int, float)) and math.isfinite(word["raw_end"]) else None,
                   word["raw_text"], json.dumps(word["issues"])))
            existing = db.execute("SELECT 1 FROM source_transcripts WHERE media_id=? LIMIT 1", (job["media_id"],)).fetchone() or db.execute(
                "SELECT 1 FROM source_transcript_meta WHERE media_id=? LIMIT 1", (job["media_id"],)).fetchone()
            applied = 0
            if not existing:
                for word in words:
                    if not word["timed"]: continue
                    db.execute("""INSERT INTO source_transcripts
                       (id,media_id,start_ms,end_ms,precision,text,language,granularity,provenance_json,
                        review_status,asr_run_id,asr_candidate_id,word_order,edit_status,attention_json,created_at,updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,'unreviewed',?,?,?,'machine',?,?,?)""",
                       (str(uuid4()), job["media_id"], word["start_ms"], word["end_ms"], "estimated",
                        word["text"], "en", "word", json.dumps({"method": "stable-ts", "model": job["model"], "device": device, "confidence": word["confidence"]}, ensure_ascii=False),
                        run_id, candidate_ids[word["order"]], word["order"], json.dumps(word["issues"]), now, now))
                applied = 1
                db.execute("INSERT OR IGNORE INTO source_transcript_meta (media_id,revision,updated_at) VALUES (?,1,?)",
                           (job["media_id"], now))
            db.execute("UPDATE asr_jobs SET status='completed',device=?,finished_at=?,applied=? WHERE id=?",
                       (device, now, applied, job["id"]))
            db.execute("UPDATE projects SET updated_at=? WHERE id=?", (now, media["project_id"]))
        return True
    except Exception as exc:
        with closing(connect(db_path)) as db, db:
            cancelled = db.execute("SELECT cancel_requested FROM asr_jobs WHERE id=?", (job["id"],)).fetchone()
            if cancelled and cancelled[0]:
                db.execute("UPDATE asr_jobs SET status='cancelled',finished_at=? WHERE id=?", (timestamp(), job["id"]))
            else:
                message = str(exc) if isinstance(exc, RuntimeError) else f"{type(exc).__name__}: {exc}"
                db.execute("UPDATE asr_jobs SET status='failed',error=?,finished_at=? WHERE id=?", (message[:500], timestamp(), job["id"]))
        return True


class ASRWorker:
    def __init__(self, db_path: Path, data_dir: Path):
        self.db_path, self.data_dir = db_path, data_dir
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._loop, name="simulst-asr", daemon=True)

    def start(self) -> None:
        recover_interrupted(self.db_path)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=1)

    def _loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                worked = process_next_job(self.db_path, self.data_dir)
            except sqlite3.Error:
                worked = False
            if not worked:
                self.stop_event.wait(0.5)
