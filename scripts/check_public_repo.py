"""Conservative public-repository guard. Does not stage, delete, or rewrite data."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_PARTS = {
    "data", "personal-notes", "backups", "exports", "ocr_frames",
    "transcripts", "annotations", "private", "secrets", ".venv", "node_modules", "dist",
}
FORBIDDEN_SUFFIXES = {
    ".mp4", ".mov", ".mkv", ".wav", ".mp3", ".flac", ".sqlite", ".sqlite3",
    ".db", ".srt", ".vtt", ".csv", ".pem", ".key", ".p12",
}
TEXT_PATTERNS = {
    "email address": re.compile(rb"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    "user-home absolute path": re.compile(
        rb"(?:[A-Za-z]:[\\/](?:Users|Documents and Settings)[\\/][^\\/\s]+|/"
        + rb"Users/[^/\s]+|/" + rb"home/[^/\s]+)"
    ),
    "likely credential assignment": re.compile(rb"(?i)\b(?:api[_-]?key|access[_-]?token|secret|password)\s*[:=]\s*['\"][^'\"\s]{8,}['\"]"),
    "private key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
}


def git(*args: str) -> bytes:
    result = subprocess.run(["git", *args], cwd=ROOT, capture_output=True)
    if result.returncode:
        raise RuntimeError(result.stderr.decode("utf-8", "replace").strip())
    return result.stdout


def path_issues(name: str) -> list[str]:
    path = Path(name)
    issues = []
    if any(part.lower() in FORBIDDEN_PARTS for part in path.parts):
        issues.append("private/generated directory")
    if path.suffix.lower() in FORBIDDEN_SUFFIXES:
        issues.append("media/database/export or credential file")
    if path.name.lower().startswith(".env") and path.name != ".env.example":
        issues.append("local environment configuration")
    return issues


def content_issues(payload: bytes) -> list[str]:
    if len(payload) > 2_000_000 or b"\0" in payload:
        return []
    return [label for label, pattern in TEXT_PATTERNS.items() if pattern.search(payload)]


def main() -> int:
    try:
        staged = {item.decode("utf-8", "replace") for item in git("ls-files", "-z", "--cached").split(b"\0") if item}
        visible = {item.decode("utf-8", "replace") for item in git("ls-files", "-z", "--others", "--exclude-standard").split(b"\0") if item}
        history = {item.decode("utf-8", "replace") for item in git("log", "--all", "--name-only", "--pretty=format:", "-z").split(b"\0") if item}
    except RuntimeError as exc:
        print(f"Repository check unavailable: {exc}", file=sys.stderr)
        return 2
    failures: list[str] = []
    for name in sorted(staged | visible):
        for issue in path_issues(name):
            failures.append(f"{name}: {issue}")
        path = ROOT / name
        if path.is_file():
            for issue in content_issues(path.read_bytes()):
                failures.append(f"{name}: {issue} in working file")
        if name in staged:
            try:
                indexed = git("show", f":{name}")
            except RuntimeError:
                indexed = b""
            for issue in content_issues(indexed):
                failures.append(f"{name}: {issue} in staged content")
    for name in sorted(history):
        for issue in path_issues(name):
            failures.append(f"Git history: {name}: {issue}")
    if failures:
        print("Public repository check FAILED:\n" + "\n".join(f"- {issue}" for issue in failures))
        return 1
    print(f"Public repository check passed: {len(staged)} tracked/staged, {len(visible)} visible untracked files; {len(history)} historical paths checked.")
    print("Review staged files manually before every public push. This checker is not a privacy guarantee.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
