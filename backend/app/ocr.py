"""Local, versioned target-subtitle OCR. Raw samples never inherit human corrections."""
import json
import re
import sqlite3
import subprocess
import threading
from contextlib import closing
from dataclasses import dataclass
from dataclasses import replace
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from typing import Callable, Iterable, Protocol
from uuid import uuid4

from .analysis_contracts import SubtitleROI
from .db import connect


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Cancelled(Exception):
    pass


@dataclass(frozen=True)
class Sample:
    frame_ms: int
    text: str
    confidence: float | None = None
    reused: bool = False


class OCREngine(Protocol):
    def scan(self, media_path: Path, params: dict, cancelled: Callable[[], bool]) -> tuple[Iterable[Sample], str]: ...


def validate_roi(roi: dict) -> SubtitleROI:
    values = [roi.get(key) for key in ("x", "y", "width", "height")]
    if any(not isinstance(value, (int, float)) or isinstance(value, bool) for value in values):
        raise ValueError("ROI coordinates must be numbers")
    x, y, width, height = map(float, values)
    if x < 0 or y < 0 or width <= 0 or height <= 0 or x + width > 1 or y + height > 1:
        raise ValueError("ROI must lie inside the original video frame")
    return SubtitleROI(x, y, width, height)


def build_states(samples: list[Sample], end_ms: int) -> list[tuple[int, int, str, list[int], float | None]]:
    """Normalized formatting may match; real text changes remain separate."""
    states: list[tuple[int, int, str, list[int], float | None]] = []
    for index, sample in enumerate(samples):
        if states and normalize_text(sample.text) == normalize_text(states[-1][2]):
            start, _, text, indices, confidence = states[-1]
            scores = [value for value in (confidence, sample.confidence) if value is not None]
            states[-1] = (start, min(end_ms, samples[index + 1].frame_ms if index + 1 < len(samples) else end_ms),
                          text, indices + [index], min(scores) if scores else None)
        else:
            next_ms = samples[index + 1].frame_ms if index + 1 < len(samples) else end_ms
            states.append((sample.frame_ms, min(end_ms, next_ms), sample.text, [index], sample.confidence))
    return [state for state in states if state[1] > state[0]]


def normalize_text(value: str) -> str:
    compact = re.sub(r"\s+", " ", value).strip()
    return re.sub(r"(?<=[\u3400-\u9fff]) (?=[\u3400-\u9fff])", "", compact)


def stabilize_samples(samples: list[Sample], confidence_floor: float = .55) -> tuple[list[Sample], int]:
    """Suppress only a low-confidence A-B-A flicker; retain every raw observation."""
    result = list(samples)
    changed = 0
    for index in range(1, len(samples) - 1):
        before, current, after = samples[index - 1:index + 2]
        if (current.text and normalize_text(before.text) == normalize_text(after.text)
                and normalize_text(current.text) != normalize_text(before.text)
                and current.confidence is not None and current.confidence < confidence_floor
                and (before.confidence or 0) >= .75 and (after.confidence or 0) >= .75):
            result[index] = replace(current, text=before.text)
            changed += 1
    return result, changed


class PaddleEngine:
    def __init__(self):
        self._recognizer = None

    def scan(self, media_path: Path, params: dict, cancelled: Callable[[], bool]) -> tuple[list[Sample], str]:
        import cv2
        import numpy as np
        from paddleocr import PaddleOCR

        roi = validate_roi(params["roi"])
        probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                "stream=width,height", "-of", "json", str(media_path)],
                               capture_output=True, text=True, check=True)
        stream = json.loads(probe.stdout)["streams"][0]
        frame_width, frame_height = int(stream["width"]), int(stream["height"])
        left = max(0, min(frame_width - 2, round(roi.x * frame_width) // 2 * 2))
        top = max(0, min(frame_height - 2, round(roi.y * frame_height) // 2 * 2))
        width = max(2, min(frame_width - left, round(roi.width * frame_width) // 2 * 2))
        height = max(2, min(frame_height - top, round(roi.height * frame_height) // 2 * 2))
        # Explicit mobile v5 models keep Chinese support while making local CPU review practical.
        if self._recognizer is None:
            self._recognizer = PaddleOCR(lang="ch", ocr_version="PP-OCRv5",
                               text_detection_model_name="PP-OCRv5_mobile_det",
                               text_recognition_model_name="PP-OCRv5_mobile_rec",
                               use_doc_orientation_classify=False,
                               use_doc_unwarping=False, use_textline_orientation=False)
        fps = params["fps"]
        command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{params['start_ms']/1000:.3f}",
                   "-t", f"{(params['end_ms']-params['start_ms'])/1000:.3f}", "-i", str(media_path),
                   "-vf", f"fps={fps},crop={width}:{height}:{left}:{top}", "-pix_fmt", "bgr24",
                   "-f", "rawvideo", "pipe:1"]
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        samples: list[Sample] = []
        last_preview = None
        last_text, last_confidence = "", None
        try:
            frame_bytes = width * height * 3
            index = 0
            while True:
                if cancelled(): raise Cancelled()
                frame = process.stdout.read(frame_bytes)
                if not frame: break
                if len(frame) != frame_bytes: raise RuntimeError("FFmpeg returned a partial frame")
                frame_ms = params["start_ms"] + round(index * 1000 / fps)
                index += 1
                if frame_ms >= params["end_ms"]: break
                image = np.frombuffer(frame, dtype=np.uint8).reshape(height, width, 3)
                preview = cv2.resize(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), (128, 64), interpolation=cv2.INTER_AREA)
                difference = cv2.absdiff(preview, last_preview) if last_preview is not None else None
                # A few high-contrast pixels can be a single newly rendered character.
                reused = difference is not None and float(np.mean(difference)) < params["crop_change_threshold"] \
                    and int(np.count_nonzero(difference > 30)) < 8
                if not reused:
                    result = list(self._recognizer.predict(image))
                    data = result[0].json if result else {}
                    data = data.get("res", data)
                    texts = [str(text).strip() for text in data.get("rec_texts", []) if str(text).strip()]
                    scores = [float(score) for score in data.get("rec_scores", [])]
                    last_text = "\n".join(texts)
                    last_confidence = min(scores) if scores else None
                    last_preview = preview
                samples.append(Sample(frame_ms, last_text, last_confidence, reused))
            if process.poll() is None: process.communicate()
            if process.returncode not in (0, None):
                raise RuntimeError("FFmpeg frame sampling failed")
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()
        if not samples: raise RuntimeError("No video frames in selected OCR range")
        return samples, version("paddleocr")


def enqueue(db_path: Path, media_id: str, params: dict) -> sqlite3.Row:
    validate_roi(params["roi"])
    if not (1 <= params["fps"] <= 15) or params["end_ms"] <= params["start_ms"]:
        raise ValueError("Choose a valid time range and 1–15 frames per second")
    with closing(connect(db_path)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        media = db.execute("SELECT duration_ms FROM media_assets WHERE id=?", (media_id,)).fetchone()
        if not media: raise ValueError("Media not found")
        if params["start_ms"] < 0 or params["end_ms"] > media["duration_ms"]:
            raise ValueError("OCR range must fit the media")
        if params.get("mode", "range") == "range" and params["end_ms"] - params["start_ms"] > 120000:
            raise ValueError("Selected OCR range must be at most 120 seconds; choose Entire Video for longer media")
        active = db.execute("SELECT * FROM ocr_jobs WHERE media_id=? AND status IN ('queued','running')", (media_id,)).fetchone()
        if active: return active
        job_id, run_id = str(uuid4()), str(uuid4())
        db.execute("INSERT INTO ocr_jobs (id,media_id,status,parameters_json,requested_at) VALUES (?,?,'queued',?,?)",
                   (job_id, media_id, json.dumps(params), now()))
        db.execute("INSERT INTO ocr_runs (id,job_id,media_id,engine,engine_version,parameters_json,created_at) VALUES (?,?,?,?,?,?,?)",
                   (run_id, job_id, media_id, "paddleocr", "pending", json.dumps(params), now()))
        chunk_ms, overlap = params.get("chunk_ms", 30000), params.get("overlap_ms", 200)
        for index, start in enumerate(range(params["start_ms"], params["end_ms"], chunk_ms)):
            end = min(start + chunk_ms, params["end_ms"])
            db.execute("""INSERT INTO ocr_job_chunks
                (id,job_id,chunk_index,core_start_ms,core_end_ms,sample_start_ms,sample_end_ms,status)
                VALUES (?,?,?,?,?,?,?,'queued')""",
                (str(uuid4()), job_id, index, start, end, max(params["start_ms"], start - overlap),
                 min(params["end_ms"], end + overlap)))
        return db.execute("SELECT * FROM ocr_jobs WHERE id=?", (job_id,)).fetchone()


def cancel(db_path: Path, job_id: str) -> sqlite3.Row | None:
    with closing(connect(db_path)) as db, db:
        row = db.execute("SELECT * FROM ocr_jobs WHERE id=?", (job_id,)).fetchone()
        if not row: return None
        if row["status"] == "queued":
            db.execute("UPDATE ocr_jobs SET status='cancelled',cancel_requested=1,finished_at=? WHERE id=?", (now(), job_id))
        elif row["status"] == "running":
            db.execute("UPDATE ocr_jobs SET cancel_requested=1 WHERE id=?", (job_id,))
        return db.execute("SELECT * FROM ocr_jobs WHERE id=?", (job_id,)).fetchone()


def recover_interrupted(db_path: Path) -> None:
    with closing(connect(db_path)) as db, db:
        db.execute("UPDATE ocr_job_chunks SET status='queued',started_at=NULL WHERE status='running'")
        db.execute("UPDATE ocr_jobs SET status='queued',error=NULL WHERE status='running' AND cancel_requested=0")
        db.execute("UPDATE ocr_jobs SET status='cancelled',finished_at=? WHERE status='running' AND cancel_requested=1", (now(),))


def resume(db_path: Path, job_id: str) -> sqlite3.Row | None:
    with closing(connect(db_path)) as db, db:
        row = db.execute("SELECT * FROM ocr_jobs WHERE id=?", (job_id,)).fetchone()
        if not row or row["status"] not in ("failed", "cancelled"): return row
        db.execute("UPDATE ocr_job_chunks SET status='queued',error=NULL WHERE job_id=? AND status!='completed'", (job_id,))
        db.execute("UPDATE ocr_jobs SET status='queued',error=NULL,cancel_requested=0,finished_at=NULL WHERE id=?", (job_id,))
        return db.execute("SELECT * FROM ocr_jobs WHERE id=?", (job_id,)).fetchone()


def process_next_job(db_path: Path, data_dir: Path, engine: OCREngine | None = None) -> bool:
    with closing(connect(db_path)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        job = db.execute("SELECT * FROM ocr_jobs WHERE status='queued' ORDER BY requested_at LIMIT 1").fetchone()
        if not job: return False
        db.execute("UPDATE ocr_jobs SET status='running',started_at=? WHERE id=?", (now(), job["id"]))
        job = dict(job)
    def cancelled() -> bool:
        with closing(connect(db_path)) as db:
            return bool(db.execute("SELECT cancel_requested FROM ocr_jobs WHERE id=?", (job["id"],)).fetchone()[0])
    current_chunk = None
    try:
        with closing(connect(db_path)) as db:
            media = db.execute("SELECT original_path FROM media_assets WHERE id=?", (job["media_id"],)).fetchone()
        if not media: raise RuntimeError("Media record is missing")
        media_path = Path(media["original_path"]).resolve()
        if data_dir.resolve() not in media_path.parents or not media_path.is_file():
            raise RuntimeError("Original media is unavailable in private storage")
        params = json.loads(job["parameters_json"])
        recognizer = engine or PaddleEngine()
        with closing(connect(db_path)) as db, db:
            run_id = db.execute("SELECT id FROM ocr_runs WHERE job_id=?", (job["id"],)).fetchone()[0]
            chunks = db.execute("SELECT * FROM ocr_job_chunks WHERE job_id=? ORDER BY chunk_index", (job["id"],)).fetchall()
        for chunk in chunks:
            if chunk["status"] == "completed": continue
            if cancelled(): raise Cancelled()
            current_chunk = chunk["id"]
            with closing(connect(db_path)) as db, db:
                db.execute("UPDATE ocr_job_chunks SET status='running',started_at=?,error=NULL WHERE id=?", (now(), current_chunk))
            scan_params = {**params, "start_ms": chunk["sample_start_ms"], "end_ms": chunk["sample_end_ms"]}
            scanned, engine_version = recognizer.scan(media_path, scan_params, cancelled)
            samples = list(scanned)
            if cancelled(): raise Cancelled()
            with closing(connect(db_path)) as db, db:
                db.execute("BEGIN IMMEDIATE")
                duplicate_count = 0
                for sample in samples:
                    inserted = db.execute("""INSERT OR IGNORE INTO ocr_observations
                        (id,media_id,frame_ms,raw_text,roi_json,provenance_json,run_id) VALUES (?,?,?,?,?,?,?)""",
                        (str(uuid4()), job["media_id"], sample.frame_ms, sample.text, json.dumps(params["roi"]),
                         json.dumps({"method": "crop_reuse" if sample.reused else "paddleocr", "model": "PP-OCRv5_mobile",
                                     "confidence": sample.confidence, "ocr_status": "recognized" if sample.text else "blank",
                                     "parameters": params, "chunk_index": chunk["chunk_index"]}), run_id)).rowcount
                    duplicate_count += 1 - inserted
                db.execute("""UPDATE ocr_job_chunks SET status='completed',finished_at=?,frames_sampled=?,
                    ocr_calls=?,duplicates_consolidated=? WHERE id=?""",
                    (now(), len(samples), sum(not sample.reused for sample in samples), duplicate_count, current_chunk))
                db.execute("UPDATE ocr_runs SET engine_version=? WHERE id=?", (engine_version, run_id))
            current_chunk = None
        with closing(connect(db_path)) as db, db:
            observations = db.execute("SELECT * FROM ocr_observations WHERE run_id=? ORDER BY frame_ms,id", (run_id,)).fetchall()
            if not observations: raise RuntimeError("No OCR observations in selected range")
            raw_samples = [Sample(row["frame_ms"], row["raw_text"], json.loads(row["provenance_json"]).get("confidence"),
                                  json.loads(row["provenance_json"]).get("method") == "crop_reuse") for row in observations]
            stable_samples, stabilized = stabilize_samples(raw_samples)
            states = build_states(stable_samples, params["end_ms"])
            if cancelled(): raise Cancelled()
            db.execute("BEGIN IMMEDIATE")
            for start, end, text, indices, confidence in states:
                uncertain = confidence is None or confidence < params["confidence_review_threshold"] or len(indices) == 1
                db.execute("""INSERT INTO subtitle_screen_states
                    (id,media_id,start_ms,end_ms,precision,text,observation_ids_json,provenance_json,review_status,run_id)
                    VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (str(uuid4()), job["media_id"], start, end, "estimated", text,
                     json.dumps([observations[index]["id"] for index in indices]),
                     json.dumps({"method": "paddleocr", "model": "PP-OCRv5_mobile", "confidence": confidence,
                                 "needs_review": uncertain, "uncertain_transition": uncertain,
                                 "stabilized_observations": stabilized}), "unreviewed", run_id))
            db.execute("UPDATE ocr_jobs SET status='completed',finished_at=? WHERE id=?", (now(), job["id"]))
        return True
    except Cancelled:
        with closing(connect(db_path)) as db, db:
            if current_chunk: db.execute("UPDATE ocr_job_chunks SET status='queued',started_at=NULL WHERE id=?", (current_chunk,))
            db.execute("UPDATE ocr_jobs SET status='cancelled',finished_at=? WHERE id=?", (now(), job["id"]))
        return True
    except Exception as exc:
        with closing(connect(db_path)) as db, db:
            if current_chunk: db.execute("UPDATE ocr_job_chunks SET status='failed',error=?,finished_at=? WHERE id=?",
                                         (f"{type(exc).__name__}: {exc}"[:500], now(), current_chunk))
            db.execute("UPDATE ocr_jobs SET status='failed',error=?,finished_at=? WHERE id=?",
                       (f"{type(exc).__name__}: {exc}"[:500], now(), job["id"]))
        return True


class OCRWorker:
    def __init__(self, db_path: Path, data_dir: Path):
        self.db_path, self.data_dir = db_path, data_dir
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._loop, name="simulst-ocr", daemon=True)

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
