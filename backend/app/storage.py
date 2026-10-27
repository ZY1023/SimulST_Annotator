"""Read-only inventory of private files; never delete files during an audit."""
from pathlib import Path


def inventory(data_dir: Path, media_rows: list) -> list[dict]:
    references: dict[Path, list[str]] = {}
    for media in media_rows:
        for key in ("original_path", "audio_path"):
            path = Path(media[key]).resolve()
            references.setdefault(path, []).append(media["id"])
    result = []
    for path in sorted(data_dir.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(data_dir).as_posix()
        linked = references.get(path.resolve(), [])
        if linked:
            category = "referenced_media"
        elif relative == "simulst.sqlite3":
            category = "active_database"
        elif path.suffix.lower() in {".sqlite3", ".db"} or relative.startswith("backups/"):
            category = "backup_or_legacy_database"
        elif relative.startswith("models/"):
            category = "model_cache"
        elif relative == "synthetic-demo.mp4":
            category = "standalone_demo"
        else:
            category = "potentially_orphaned"
        result.append({
            "relative_path": relative,
            "size_bytes": path.stat().st_size,
            "reference_count": len(linked),
            "media_ids": linked,
            "category": category,
        })
    for path, linked in references.items():
        if not path.is_file():
            result.append({
                "relative_path": path.relative_to(data_dir).as_posix() if path.is_relative_to(data_dir) else "outside-private-root",
                "size_bytes": 0, "reference_count": len(linked),
                "media_ids": linked, "category": "missing_referenced_file",
            })
    return result
