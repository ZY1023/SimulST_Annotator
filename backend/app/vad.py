"""Independent, local Silero source-speech activity jobs."""
import json
import sqlite3
import threading
import wave
from array import array
from contextlib import closing
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from typing import Callable, Protocol
from uuid import uuid4

from .db import connect

PARAMETERS = {"sampling_rate_hz": 16000, "threshold": 0.5,
              "min_speech_duration_ms": 250, "min_silence_duration_ms": 100,
              "speech_pad_ms": 30}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Cancelled(Exception):
    pass


class VADEngine(Protocol):
    def detect(self, audio_path: Path, cancelled: Callable[[], bool]) -> tuple[list[tuple[int, int]], str]: ...


class SileroEngine:
    def detect(self, audio_path: Path, cancelled: Callable[[], bool]) -> tuple[list[tuple[int, int]], str]:
        try:
            import numpy as np
            import torch
            from silero_vad import get_speech_timestamps, load_silero_vad
        except ImportError as exc:
            raise RuntimeError("Silero VAD is not installed. Install backend/requirements-vad.txt.") from exc
        with wave.open(str(audio_path), "rb") as audio:
            if (audio.getframerate(), audio.getnchannels(), audio.getsampwidth()) != (16000, 1, 2):
                raise RuntimeError("Source VAD requires the extracted 16 kHz mono PCM audio")
            samples = np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2").astype(np.float32) / 32768
        if cancelled(): raise Cancelled()
        model = load_silero_vad(onnx=False)
        last_percent = -1
        def progress(percent: float) -> None:
            nonlocal last_percent
            milestone = int(percent // 2)
            if milestone != last_percent:
                last_percent = milestone
                if cancelled(): raise Cancelled()
        result = get_speech_timestamps(torch.from_numpy(samples), model, sampling_rate=16000,
            threshold=PARAMETERS["threshold"], min_speech_duration_ms=PARAMETERS["min_speech_duration_ms"],
            min_silence_duration_ms=PARAMETERS["min_silence_duration_ms"],
            speech_pad_ms=PARAMETERS["speech_pad_ms"], progress_tracking_callback=progress)
        if cancelled(): raise Cancelled()
        return [(round(item["start"] / 16), round(item["end"] / 16)) for item in result], version("silero-vad")


def normalize_intervals(speech: list[tuple[int, int]], duration_ms: int) -> list[tuple[str, int, int]]:
    """Merge padded overlaps, then derive the exact nonspeech complement."""
    merged: list[list[int]] = []
    for start, end in sorted(speech):
        start, end = max(0, min(duration_ms, start)), max(0, min(duration_ms, end))
        if end <= start: continue
        if merged and start <= merged[-1][1]: merged[-1][1] = max(merged[-1][1], end)
        else: merged.append([start, end])
    intervals: list[tuple[str, int, int]] = []
    cursor = 0
    for start, end in merged:
        if cursor < start: intervals.append(("nonspeech", cursor, start))
        intervals.append(("speech", start, end))
        cursor = end
    if cursor < duration_ms: intervals.append(("nonspeech", cursor, duration_ms))
    return intervals


def enqueue(db_path: Path, media_id: str) -> sqlite3.Row:
    with closing(connect(db_path)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        if not db.execute("SELECT 1 FROM media_assets WHERE id=?", (media_id,)).fetchone():
            raise ValueError("Media not found")
        active = db.execute("SELECT * FROM vad_jobs WHERE media_id=? AND status IN ('queued','running')", (media_id,)).fetchone()
        if active: return active
        job_id = str(uuid4())
        db.execute("INSERT INTO vad_jobs (id,media_id,status,requested_at) VALUES (?,?,'queued',?)", (job_id, media_id, now()))
        return db.execute("SELECT * FROM vad_jobs WHERE id=?", (job_id,)).fetchone()


def cancel(db_path: Path, job_id: str) -> sqlite3.Row | None:
    with closing(connect(db_path)) as db, db:
        row = db.execute("SELECT * FROM vad_jobs WHERE id=?", (job_id,)).fetchone()
        if not row: return None
        if row["status"] == "queued":
            db.execute("UPDATE vad_jobs SET status='cancelled',cancel_requested=1,finished_at=? WHERE id=?", (now(), job_id))
        elif row["status"] == "running":
            db.execute("UPDATE vad_jobs SET cancel_requested=1 WHERE id=?", (job_id,))
        return db.execute("SELECT * FROM vad_jobs WHERE id=?", (job_id,)).fetchone()


def recover_interrupted(db_path: Path) -> None:
    with closing(connect(db_path)) as db, db:
        db.execute("UPDATE vad_jobs SET status='failed',error='Interrupted by restart; run again.',finished_at=? WHERE status='running'", (now(),))


def process_next_job(db_path: Path, data_dir: Path, engine: VADEngine | None = None) -> bool:
    with closing(connect(db_path)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        job = db.execute("SELECT * FROM vad_jobs WHERE status='queued' ORDER BY requested_at LIMIT 1").fetchone()
        if not job: return False
        db.execute("UPDATE vad_jobs SET status='running',started_at=? WHERE id=?", (now(), job["id"]))
        job = dict(job)
    def is_cancelled() -> bool:
        with closing(connect(db_path)) as db:
            row = db.execute("SELECT cancel_requested FROM vad_jobs WHERE id=?", (job["id"],)).fetchone()
            return bool(row and row[0])
    try:
        with closing(connect(db_path)) as db:
            media = db.execute("SELECT audio_path,duration_ms FROM media_assets WHERE id=?", (job["media_id"],)).fetchone()
        if not media: raise RuntimeError("Media record is missing")
        audio_path = Path(media["audio_path"]).resolve()
        if data_dir.resolve() not in audio_path.parents or not audio_path.is_file():
            raise RuntimeError("Extracted audio is unavailable in private storage")
        speech, model_version = (engine or SileroEngine()).detect(audio_path, is_cancelled)
        intervals = normalize_intervals(speech, media["duration_ms"])
        with closing(connect(db_path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            if is_cancelled(): raise Cancelled()
            run_id, created = str(uuid4()), now()
            db.execute("INSERT INTO vad_runs (id,job_id,media_id,engine,model_version,parameters_json,created_at) VALUES (?,?,?,?,?,?,?)",
                       (run_id, job["id"], job["media_id"], "silero-vad", model_version, json.dumps(PARAMETERS), created))
            for kind, start, end in intervals:
                db.execute("INSERT INTO vad_intervals (id,run_id,media_id,kind,start_ms,end_ms) VALUES (?,?,?,?,?,?)",
                           (str(uuid4()), run_id, job["media_id"], kind, start, end))
            db.execute("UPDATE vad_jobs SET status='completed',finished_at=? WHERE id=?", (created, job["id"]))
        return True
    except Cancelled:
        with closing(connect(db_path)) as db, db:
            db.execute("UPDATE vad_jobs SET status='cancelled',finished_at=? WHERE id=?", (now(), job["id"]))
        return True
    except Exception as exc:
        with closing(connect(db_path)) as db, db:
            db.execute("UPDATE vad_jobs SET status='failed',error=?,finished_at=? WHERE id=?",
                       (f"{type(exc).__name__}: {exc}"[:500], now(), job["id"]))
        return True


class VADWorker:
    def __init__(self, db_path: Path, data_dir: Path):
        self.db_path, self.data_dir = db_path, data_dir
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._loop, name="simulst-vad", daemon=True)

    def start(self) -> None:
        recover_interrupted(self.db_path)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=1)

    def _loop(self) -> None:
        while not self.stop_event.is_set():
            try: worked = process_next_job(self.db_path, self.data_dir)
            except sqlite3.Error: worked = False
            if not worked: self.stop_event.wait(0.5)
