"""Descriptive timing events from visible subtitle states; no semantic alignment."""
from .ocr import normalize_text


def overlap_ms(start: int, end: int, speech: list[dict]) -> int:
    spans = sorted((max(start, row["start_ms"]), min(end, row["end_ms"])) for row in speech
                   if row["end_ms"] > start and row["start_ms"] < end)
    total, cursor = 0, start
    for left, right in spans:
        if right > max(left, cursor):
            total += right - max(left, cursor)
            cursor = right
    return total


def derive(states: list[dict], speech: list[dict], gap_threshold_ms: int = 2000,
           run_id: str | None = None, vad_run_id: str | None = None) -> dict[str, list[dict]]:
    if gap_threshold_ms < 1: raise ValueError("Gap threshold must be positive")
    ordered = sorted((row for row in states if row.get("active", 1)), key=lambda row: (row["start_ms"], row["id"]))
    settings = {"gap_threshold_ms": gap_threshold_ms, "ocr_run_id": run_id, "vad_run_id": vad_run_id,
                "time_unit": "ms", "source_alignment": "temporal_overlap_only"}
    updates, gaps, dwell, blanks, intervals = [], [], [], [], []
    for row in ordered:
        start, end = row["start_ms"], row["end_ms"]
        duration = end - start
        if duration <= 0: continue
        base = {"id": f"{row['id']}:dwell", "media_id": row["media_id"], "state_id": row["id"],
                "start_ms": start, "end_ms": end, "duration_ms": duration,
                "text": row["text"], "precision": row.get("precision", "estimated"), "parameters": settings}
        dwell.append(base)
        if not normalize_text(row["text"]): blanks.append({**base, "id": f"{row['id']}:blank"})
    # Adjacent identical screen states form one continuous period without an update.
    periods: list[dict] = []
    for row in ordered:
        if row["end_ms"] <= row["start_ms"]: continue
        if periods and normalize_text(periods[-1]["text"]) == normalize_text(row["text"]) and abs(row["start_ms"] - periods[-1]["end_ms"]) <= 250:
            periods[-1]["end_ms"] = max(periods[-1]["end_ms"], row["end_ms"])
            periods[-1]["state_ids"].append(row["id"])
        else:
            periods.append({"start_ms": row["start_ms"], "end_ms": row["end_ms"], "text": row["text"],
                            "media_id": row["media_id"], "state_ids": [row["id"]], "precision": row.get("precision", "estimated")})
    for index, row in enumerate(periods):
        start, end = row["start_ms"], row["end_ms"]
        duration = end - start
        if duration >= gap_threshold_ms:
            spoken = overlap_ms(start, end, speech)
            gaps.append({"id": f"{row['state_ids'][0]}:gap", "media_id": row["media_id"],
                         "state_ids": row["state_ids"], "start_ms": start, "end_ms": end,
                         "duration_ms": duration, "blank_screen": not bool(normalize_text(row["text"])),
                         "source_speech_overlap_ms": spoken, "source_speech_active": spoken > 0,
                         "precision": row["precision"], "parameters": settings})
        if index == 0: continue
        previous = periods[index - 1]
        # A missing or unreviewed intervening state makes the transition unknown.
        if abs(start - previous["end_ms"]) > 250: continue
        before, after = normalize_text(previous["text"]), normalize_text(row["text"])
        if before == after: continue
        event_type = "appearance" if not before else "disappearance" if not after else "text_change"
        updates.append({"id": f"{previous['state_ids'][-1]}:{row['state_ids'][0]}:update", "media_id": row["media_id"],
                        "start_ms": start, "end_ms": start, "before_text": previous["text"], "after_text": row["text"],
                        "event_type": event_type, "state_ids": [previous["state_ids"][-1], row["state_ids"][0]],
                        "precision": row.get("precision", "estimated"), "parameters": settings})
        intervals.append({"id": f"{previous['state_ids'][0]}:{row['state_ids'][0]}:interval", "media_id": row["media_id"],
                          "start_ms": previous["start_ms"], "end_ms": start,
                          "duration_ms": start - previous["start_ms"], "state_ids": previous["state_ids"] + row["state_ids"],
                          "parameters": settings})
    return {"target_update_events": updates, "output_gap_candidates": gaps,
            "subtitle_dwell_intervals": dwell, "blank_screen_intervals": blanks,
            "inter_update_intervals": intervals, "parameters": settings}
