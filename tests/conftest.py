import io
import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from melograb.track import Track  # noqa: E402


@pytest.fixture
def track():
    return Track(
        title="Mosquito",
        artist="PinkPantheress",
        album="Fancy That",
        album_artist="PinkPantheress",
        track_number=3,
        total_tracks=9,
        release_date="2025-05-09",
        position=1,
        duration_ms=150_000,
        cover_url="https://example.invalid/cover.jpg",
    )


@pytest.fixture
def jpeg_bytes():
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 30, 30)).save(buf, "JPEG")
    return buf.getvalue()


requires_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def make_audio(path: str) -> str:
    """One second of silence in the container implied by `path`'s extension."""
    codec = {".mp3": ["-c:a", "libmp3lame"], ".m4a": ["-c:a", "aac"], ".opus": ["-c:a", "libopus"]}
    ext = os.path.splitext(path)[1]
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", "1",
         *codec[ext], path],
        check=True,
    )
    return path
