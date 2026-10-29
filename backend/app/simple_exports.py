"""One dataset per file; the preview and download share this builder."""
import csv
import io
import json
import re
import zipfile
from dataclasses import dataclass

DATASETS = ("source_transcripts", "subtitle_screen_states", "derived_events", "ocr_observations", "vad_intervals",
            "target_update_events", "output_gap_candidates")
LABELS = {"source_transcripts": "Source words", "subtitle_screen_states": "Target subtitle states",
          "derived_events": "Text change events", "ocr_observations": "OCR observations", "vad_intervals": "Source VAD intervals",
          "target_update_events": "Target update events", "output_gap_candidates": "Output gap candidates"}
FILENAMES = {"source_transcripts": "source_words", "subtitle_screen_states": "target_subtitle_states",
             "derived_events": "text_change_events", "ocr_observations": "ocr_observations", "vad_intervals": "source_vad_intervals",
             "target_update_events": "target_update_events", "output_gap_candidates": "output_gap_candidates"}


@dataclass
class ExportFile:
    filename: str
    dataset: str
    count: int
    content: bytes


def slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", value).strip("_")[:70] or "media"


def simple_rows(dataset: str, rows: list[dict]) -> list[dict]:
    if dataset == "source_transcripts":
        return [{"start": round(row["start_ms"] / 1000, 3), "end": round(row["end_ms"] / 1000, 3),
                 "text": row["text"]} for row in rows]
    if dataset == "subtitle_screen_states":
        return [{"start": round(row["start_ms"] / 1000, 3), "end": round(row["end_ms"] / 1000, 3),
                 "text": row["text"]} for row in rows]
    if dataset == "derived_events":
        return [{"start": round(row["start_ms"] / 1000, 3), "end": round(row["end_ms"] / 1000, 3),
                 "type": row["event_type"], "before": row["before_text"], "after": row["after_text"]} for row in rows]
    if dataset == "ocr_observations":
        return [{"time": round(row["frame_ms"] / 1000, 3), "text": row["raw_text"]} for row in rows]
    if dataset == "vad_intervals":
        return [{"start": round(row["start_ms"] / 1000, 3), "end": round(row["end_ms"] / 1000, 3),
                 "activity": row["kind"]} for row in rows]
    if dataset == "target_update_events":
        return [{"time": round(row["start_ms"] / 1000, 3), "type": row["event_type"],
                 "before": row["before_text"], "after": row["after_text"]} for row in rows]
    if dataset == "output_gap_candidates":
        return [{"start": round(row["start_ms"] / 1000, 3), "end": round(row["end_ms"] / 1000, 3),
                 "duration": round(row["duration_ms"] / 1000, 3), "blank_screen": row["blank_screen"],
                 "source_speech_overlap": round(row["source_speech_overlap_ms"] / 1000, 3)} for row in rows]
    return [{"start": round(row["span"]["start_ms"] / 1000, 3),
             "end": round(row["span"]["end_ms"] / 1000, 3), "text": row["text"],
             "label": row["event_label"], "review_status": row["review_status"]} for row in rows]


def csv_bytes(rows: list[dict], columns: list[str]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value
                         for key, value in row.items()})
    return stream.getvalue().encode("utf-8-sig")


def build_files(data: dict, datasets: list[str], format: str, complete: bool = False,
                include_unreviewed: bool = False, include_rejected: bool = False) -> tuple[str, str, bytes, list[dict]]:
    if complete:
        content = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        name = "complete_project.json" if data["scope"] == "project" else "complete_media.json"
        return name, "application/json", content, [{"filename": name, "dataset": "complete_project", "count": len(data["annotations"])}]
    media = data["media_assets"]
    files: list[ExportFile] = []
    suffix = "csv" if format == "csv" else "json"
    for asset in media:
        folder = f"{slug(asset['display_name'])}_{asset['id'][:8]}/" if data["scope"] == "project" else ""
        for dataset in datasets:
            rows = [row for row in (data[dataset] if dataset in DATASETS else data["annotations"])
                    if row["media_id"] == asset["id"] and (dataset in DATASETS or row["tier_id"] == dataset.removeprefix("tier:"))]
            if dataset == "source_transcripts" and format != "detailed_json":
                rows = [row for row in rows if row.get("speech_status") != "not_spoken" and row.get("review_status") != "rejected"]
            if dataset == "subtitle_screen_states" and format != "detailed_json":
                allowed = {"reviewed"} | ({"unreviewed"} if include_unreviewed else set()) | ({"rejected"} if include_rejected else set())
                rows = [row for row in rows if row.get("review_status") in allowed and row.get("text", "").strip()]
            if data["scope"] == "project" and not rows:
                continue
            rows.sort(key=lambda row: (row.get("start_ms", row.get("frame_ms", row.get("span", {}).get("start_ms", 0))), row["id"]))
            values = rows if format == "detailed_json" else simple_rows(dataset, rows)
            name = FILENAMES[dataset] if dataset in DATASETS else f"tier_{slug(next((tier['name'] for tier in data['annotation_tiers'] if tier['id'] == dataset.removeprefix('tier:')), 'annotations'))}_{dataset.removeprefix('tier:')[:8]}"
            filename = f"{folder}{name}.{suffix}"
            if suffix == "json": content = json.dumps(values, ensure_ascii=False, indent=2).encode("utf-8")
            else:
                columns = list(values[0]) if values else (list(simple_rows(dataset, rows)[0]) if rows else
                    (["start", "end", "text"] if dataset in ("source_transcripts", "subtitle_screen_states") else
                     ["time", "text"] if dataset == "ocr_observations" else ["start", "end", "activity"] if dataset == "vad_intervals" else
                     ["time", "type", "before", "after"] if dataset == "target_update_events" else
                     ["start", "end", "duration", "blank_screen", "source_speech_overlap"] if dataset == "output_gap_candidates" else
                     ["start", "end", "type", "before", "after"] if dataset == "derived_events" else
                     ["start", "end", "text", "label", "review_status"]))
                content = csv_bytes(values, columns)
            files.append(ExportFile(filename, dataset, len(rows), content))
    listing = [{"filename": item.filename, "dataset": item.dataset, "count": item.count} for item in files]
    if len(files) == 1 and data["scope"] == "media":
        item = files[0]
        return item.filename.split("/")[-1], "text/csv" if suffix == "csv" else "application/json", item.content, listing
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for item in files: archive.writestr(item.filename, item.content)
    name = "project_selected_data.zip" if data["scope"] == "project" else "media_selected_data.zip"
    return name, "application/zip", stream.getvalue(), listing
