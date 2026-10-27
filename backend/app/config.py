"""Resolve private storage outside the public source tree."""
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = REPO_ROOT.parent / f"{REPO_ROOT.name}_data"


def resolve_data_dir(value: str | None = None) -> Path:
    configured = value if value is not None else os.environ.get("SIMULST_DATA_DIR")
    path = Path(configured).expanduser() if configured else DEFAULT_DATA_DIR
    if not path.is_absolute():
        path = REPO_ROOT.parent / path
    resolved = path.resolve()
    if resolved == REPO_ROOT or REPO_ROOT in resolved.parents:
        raise ValueError("SIMULST_DATA_DIR must be outside the application repository")
    return resolved


def private_file(path: str | Path, data_dir: Path) -> Path:
    """Reject database paths that escape private storage."""
    resolved = Path(path).resolve()
    if data_dir.resolve() not in resolved.parents:
        raise ValueError("Media path is outside private storage")
    return resolved
