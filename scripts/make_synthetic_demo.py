"""Generate a clearly fictional test video in private storage, never in the repository."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.config import resolve_data_dir  # noqa: E402


def main() -> None:
    data_dir = resolve_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    output = data_dir / "synthetic-demo.mp4"
    if output.exists():
        print(f"Synthetic demo already exists: {output}")
        return
    subprocess.run([
        "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000",
        "-t", "4", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
        str(output),
    ], check=True)
    print(f"Generated fictional test media: {output}")


if __name__ == "__main__":
    main()
