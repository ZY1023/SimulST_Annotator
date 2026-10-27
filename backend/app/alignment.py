"""Read-only timing diagnostics and explicit local stable-ts alignment previews."""
import json
import math
import wave
from array import array
from collections import Counter
from pathlib import Path
from typing import Protocol

from .asr import AlignmentChange, detect_device


class WordAligner(Protocol):
    def preview(self, audio_path: Path, words: list[dict], start_ms: int, end_ms: int,
                model_name: str, cache_dir: Path) -> list[AlignmentChange]: ...


def timing_diagnostics(db, media: dict) -> dict:
    rows = db.execute("""SELECT w.*, c.raw_start_seconds,c.raw_end_seconds,c.issues_json AS raw_issues
        FROM source_transcripts w LEFT JOIN asr_word_candidates c ON c.id=w.asr_candidate_id
        WHERE w.media_id=? ORDER BY w.start_ms,w.id""", (media["id"],)).fetchall()
    counts = Counter()
    details = []
    previous_end = None
    vad_run = db.execute("SELECT id FROM vad_runs WHERE media_id=? ORDER BY created_at DESC LIMIT 1", (media["id"],)).fetchone()
    nonspeech = [(row["start_ms"], row["end_ms"]) for row in db.execute(
        "SELECT start_ms,end_ms FROM vad_intervals WHERE run_id=? AND kind='nonspeech' ORDER BY start_ms",
        (vad_run["id"],))] if vad_run else []
    with wave.open(str(media["audio_path"]), "rb") as audio:
        sample_rate, channels, frames = audio.getframerate(), audio.getnchannels(), audio.getnframes()
    def waveform_rms(time_ms: int) -> float:
        first = max(0, min(frames, round(time_ms * sample_rate / 1000)))
        with wave.open(str(media["audio_path"]), "rb") as audio:
            audio.setpos(first)
            sample = array("h", audio.readframes(min(round(sample_rate * .04), frames - first)))
        return round(math.sqrt(sum(value * value for value in sample) / len(sample)) / 32768, 5) if sample else 0.0
    for row in rows:
        issues = list(json.loads(row["attention_json"]))
        duration = row["end_ms"] - row["start_ms"]
        if duration > 1000: issues.append("long_word_interval")
        nonspeech_overlap = sum(max(0, min(row["end_ms"], end) - max(row["start_ms"], start))
                                for start, end in nonspeech if start < row["end_ms"] and end > row["start_ms"])
        if duration > 0 and nonspeech_overlap >= 300 and nonspeech_overlap / duration >= 0.4:
            issues.append("vad_nonspeech_overlap")
        if previous_end is not None:
            if row["start_ms"] < previous_end: issues.append("overlap_previous")
            if row["start_ms"] - previous_end > 1000: issues.append("long_gap_previous")
        previous_end = max(previous_end or 0, row["end_ms"])
        if row["end_ms"] <= row["start_ms"]: issues.append("zero_duration")
        if row["asr_candidate_id"]:
            counts["linked_raw_words"] += 1
            raw_start = round(row["raw_start_seconds"] * 1000) if row["raw_start_seconds"] is not None else None
            raw_end = round(row["raw_end_seconds"] * 1000) if row["raw_end_seconds"] is not None else None
            if raw_start != row["start_ms"] or raw_end != row["end_ms"]:
                issues.append("edited_timing")
        else:
            raw_start = raw_end = None
            counts["unlinked_words"] += 1
        for issue in set(issues): counts[issue] += 1
        if issues:
            details.append({"word_id": row["id"], "start_ms": row["start_ms"], "end_ms": row["end_ms"],
                            "duration_ms": duration, "nonspeech_overlap_ms": nonspeech_overlap,
                            "raw_start_ms": raw_start, "raw_end_ms": raw_end,
                            "waveform_rms_at_start": waveform_rms(row["start_ms"]),
                            "waveform_rms_at_midpoint": waveform_rms((row["start_ms"] + row["end_ms"]) // 2),
                            "issues": sorted(set(issues)), "classification": "local_alignment_review"})
    raw_counts = Counter()
    runs = db.execute("SELECT id FROM asr_runs WHERE media_id=? ORDER BY created_at DESC LIMIT 1", (media["id"],)).fetchone()
    if runs:
        for row in db.execute("SELECT issues_json FROM asr_word_candidates WHERE run_id=?", (runs["id"],)):
            raw_counts.update(json.loads(row["issues_json"]))
    return {"media_duration_ms": media["duration_ms"], "media_offset_ms": media["media_offset_ms"],
            "audio_sample_rate_hz": sample_rate, "audio_channels": channels,
            "audio_duration_ms": round(frames / sample_rate * 1000), "editable_word_count": len(rows),
            "vad_run_id": vad_run["id"] if vad_run else None,
            "sync_assessment": "no_global_shift_evidence" if abs(round(frames / sample_rate * 1000)-media["duration_ms"]) <= 40 else "investigate_media_duration",
            "editable_issues": dict(counts), "raw_candidate_issues": dict(raw_counts), "word_details": details}


class StableTsWordAligner:
    def preview(self, audio_path: Path, words: list[dict], start_ms: int, end_ms: int,
                model_name: str, cache_dir: Path) -> list[AlignmentChange]:
        import numpy as np
        import stable_whisper

        if not words or end_ms <= start_ms or end_ms - start_ms > 30000:
            raise ValueError("Select a passage containing words, up to 30 seconds")
        with wave.open(str(audio_path), "rb") as audio:
            if audio.getnchannels() != 1 or audio.getsampwidth() != 2 or audio.getframerate() != 16000:
                raise ValueError("Alignment requires extracted 16 kHz mono PCM audio")
            first = round(start_ms * 16)
            audio.setpos(first)
            samples = np.frombuffer(audio.readframes(round((end_ms-start_ms) * 16)), dtype=np.int16).astype(np.float32) / 32768
        if samples.size == 0: raise ValueError("Selected audio range is empty")
        cache_dir.mkdir(parents=True, exist_ok=True)
        model = stable_whisper.load_model(model_name, device=detect_device(), download_root=str(cache_dir))
        result = model.align(samples, " ".join(word["text"].strip() for word in words), language="en", verbose=False)
        proposed = [item for segment in result.to_dict().get("segments", []) for item in segment.get("words", [])]
        if len(proposed) != len(words):
            raise ValueError("Alignment word count differs from corrected transcript; no timestamps were applied")
        changes = []
        for word, item in zip(words, proposed):
            if str(item.get("word", "")).strip() != word["text"].strip():
                raise ValueError("Alignment text differs from corrected transcript; no timestamps were applied")
            start, end = item.get("start"), item.get("end")
            if not all(isinstance(value, (int, float)) and math.isfinite(value) for value in (start, end)):
                raise ValueError("Alignment returned missing timestamps")
            start_absolute = start_ms + round(start * 1000)
            end_absolute = start_ms + round(end * 1000)
            if start_absolute < start_ms or end_absolute > end_ms or end_absolute <= start_absolute:
                raise ValueError("Alignment returned an unreliable interval; no timestamps were applied")
            changes.append(AlignmentChange(word["id"], word["text"], word["start_ms"], word["end_ms"],
                                           start_absolute, end_absolute))
        return changes
