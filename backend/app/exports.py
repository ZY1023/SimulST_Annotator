"""Selection-aware, privacy-safe annotation export formatting."""
import csv
import io
import json
import re
import zipfile
from datetime import datetime, timezone

RESEARCH_TYPES = ("source_transcripts", "ocr_observations", "subtitle_screen_states", "derived_events", "vad_intervals")
CSV_FIELDS = (
    "project_id", "media_id", "media_name", "tier_id", "tier_name", "record_type", "record_id",
    "start_ms", "end_ms", "duration_ms", "text", "label", "provenance", "review_status", "metadata",
)


def safe_filename(value: str) -> str:
    return re.sub(r"[^\w-]+", "_", value, flags=re.UNICODE).strip("._")[:48] or "export"


def redact_private_values(value):
    """Retain research relationships while removing local paths and credential-shaped fields."""
    if isinstance(value, dict):
        return {key: redact_private_values(item) for key, item in value.items()
                if not (key.lower().endswith("_path") or key.lower() in
                        {"path", "database", "database_path", "username", "password", "secret",
                         "api_key", "access_token", "refresh_token"})}
    if isinstance(value, list):
        return [redact_private_values(item) for item in value]
    if isinstance(value, str) and (re.match(r"^[A-Za-z]:[\\/]", value) or value.startswith("\\\\")
                                   or (value.startswith("/") and not value.startswith("/api/"))):
        return "[redacted-local-path]"
    return value


def csv_bytes(rows: list[dict]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else value
                         for key, value in row.items()})
    return stream.getvalue().encode("utf-8-sig")


def json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")


def normalized_rows(data: dict, tier_ids: set[str], categories: set[str]) -> list[dict]:
    media_names = {item["id"]: item["display_name"] for item in data["media_assets"]}
    tier_names = {item["id"]: item["name"] for item in data["annotation_tiers"]}
    project_id = data["project"]["id"]
    rows = []
    for item in data["annotations"]:
        if item["tier_id"] not in tier_ids:
            continue
        start, end = item["span"]["start_ms"], item["span"]["end_ms"]
        rows.append({"project_id": project_id, "media_id": item["media_id"],
                     "media_name": media_names[item["media_id"]], "tier_id": item["tier_id"],
                     "tier_name": tier_names[item["tier_id"]], "record_type": "human_annotation",
                     "record_id": item["id"], "start_ms": start, "end_ms": end, "duration_ms": end - start,
                     "text": item["text"], "label": item["event_label"] or "",
                     "provenance": {"method": "human"}, "review_status": item["review_status"],
                     "metadata": item["metadata"] | {"precision": item["span"]["precision"],
                                                    "source_ids": item["source_ids"]}})
    for category in RESEARCH_TYPES:
        if category not in categories:
            continue
        for item in data[category]:
            start = item.get("frame_ms", item.get("start_ms", 0))
            end = item.get("frame_ms", item.get("end_ms", start))
            metadata = {key: value for key, value in item.items() if key not in
                        {"id", "media_id", "start_ms", "end_ms", "frame_ms", "raw_text", "text",
                         "event_type", "provenance", "review_status"}}
            rows.append({"project_id": project_id, "media_id": item["media_id"],
                         "media_name": media_names[item["media_id"]], "tier_id": "", "tier_name": "",
                         "record_type": category, "record_id": item["id"], "start_ms": start,
                         "end_ms": end, "duration_ms": end - start,
                         "text": item.get("text", item.get("raw_text", item.get("after_text", ""))),
                         "label": item.get("event_type", item.get("kind", "")), "provenance": item.get("provenance", {}),
                         "review_status": item.get("review_status", "unreviewed"), "metadata": metadata})
    return rows


def build_advanced_export(data: dict, tier_ids: list[str], categories: list[str],
                          fmt: str, structure: str, include_empty_tiers: bool) -> tuple[bytes, str, str]:
    data = redact_private_values(data)
    selected = set(tier_ids)
    special = set(categories)
    tiers = [tier for tier in data["annotation_tiers"] if tier["id"] in selected]
    selected_annotations = [row for row in data["annotations"] if row["tier_id"] in selected]
    if not include_empty_tiers:
        populated = {row["tier_id"] for row in selected_annotations}
        tiers = [tier for tier in tiers if tier["id"] in populated]
    raw = {key: data[key] if key in special else [] for key in RESEARCH_TYPES}
    combined = {"schema_version": 2, "exported_at": datetime.now(timezone.utc).isoformat(),
                "scope": data["scope"], "project": data["project"],
                "media_assets": data["media_assets"], "annotation_tiers": tiers,
                "annotations": selected_annotations, **raw}
    rows = normalized_rows(data, selected, special)
    files: dict[str, bytes] = {}
    formats = ("json", "csv") if fmt == "both" else (fmt,)
    if structure in {"combined", "both"}:
        if "json" in formats:
            files["combined/all_annotations.json"] = json_bytes(combined)
        if "csv" in formats:
            files["combined/all_annotations.csv"] = csv_bytes(rows)
    if structure in {"separate", "both"}:
        for tier in tiers:
            tier_rows = [row for row in data["annotations"] if row["tier_id"] == tier["id"]]
            media_name = next(item["display_name"] for item in data["media_assets"] if item["id"] == tier["media_id"])
            base = f"media/{safe_filename(media_name)}_{tier['media_id'][:6]}/tiers/{safe_filename(tier['name'])}_{tier['id'][-6:]}"
            if "json" in formats:
                files[base + ".json"] = json_bytes({"schema_version": 2, "project": data["project"],
                    "media": next(item for item in data["media_assets"] if item["id"] == tier["media_id"]),
                    "tier": tier, "annotations": tier_rows})
            if "csv" in formats:
                files[base + ".csv"] = csv_bytes([row for row in rows if row["tier_id"] == tier["id"]])
        for category in RESEARCH_TYPES:
            if category not in special:
                continue
            if "json" in formats:
                files[f"research_data/{category}.json"] = json_bytes(data[category])
            if "csv" in formats:
                files[f"research_data/{category}.csv"] = csv_bytes(
                    [row for row in rows if row["record_type"] == category])
    if not files:
        raise ValueError("No files match the selected datasets and options")
    scope_name = data["media_assets"][0]["display_name"] if data["scope"] == "media" else data["project"]["name"]
    filename = safe_filename(scope_name) + "_annotations"
    if len(files) == 1:
        path, payload = next(iter(files.items()))
        suffix = path.rsplit(".", 1)[-1]
        return payload, "application/json" if suffix == "json" else "text/csv", filename + "." + suffix
    manifest = {"schema_version": 2, "software": "SimulST Annotator", "exported_at": combined["exported_at"],
                "scope": data["scope"], "project_id": data["project"]["id"],
                "media_ids": [item["id"] for item in data["media_assets"]],
                "selected_tier_ids": tier_ids, "selected_research_data": categories,
                "format": fmt, "structure": structure, "include_empty_tiers": include_empty_tiers,
                "record_counts": {"human_annotations": len(selected_annotations),
                                  **{key: len(raw[key]) for key in RESEARCH_TYPES}},
                "files": sorted(files)}
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json_bytes(manifest))
        for path, payload in files.items():
            archive.writestr(path, payload)
    return output.getvalue(), "application/zip", filename + ".zip"

