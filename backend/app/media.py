"""Media probing and derivative audio extraction; original uploads stay intact."""
import json
import subprocess
from pathlib import Path


def seconds_to_ms(seconds: str | float) -> int:
    return round(float(seconds) * 1000)


def probe_duration_ms(path: Path) -> int:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    )
    return seconds_to_ms(json.loads(result.stdout)["format"]["duration"])


def extract_audio(source: Path, destination: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(source),
         "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(destination)],
        capture_output=True, text=True, check=True,
    )

