"""Diagnostics: is everything melograb needs present and working?

Run with `melograb --self-test [--online]` (or `melograb.app/Contents/MacOS/
melograb --self-test` for the packaged app, or Tools > Run Diagnostics).
The offline part checks imports, bundled data, external tools and a real
convert+tag round trip; --online also fetches a public album and downloads
one song.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Callable, Optional

# A long-lived, public album to test against (Daft Punk - Discovery).
ONLINE_ALBUM = "https://open.spotify.com/album/2noRn2Aes5aoNVsU6iWThc"


class _Report:
    def __init__(self, emit: Callable[[str], None]):
        self.emit = emit
        self.failures = 0

    def check(self, name: str, fn: Callable[[], str], required: bool = True) -> Optional[str]:
        start = time.monotonic()
        try:
            detail = fn() or ""
        except Exception as e:  # noqa: BLE001
            mark = "FAIL" if required else "warn"
            if required:
                self.failures += 1
            self.emit(f"[{mark}] {name}: {type(e).__name__}: {e}")
            return None
        self.emit(f"[ ok ] {name}{': ' + detail if detail else ''}  ({time.monotonic() - start:.1f}s)")
        return detail


def _tool_version(name: str) -> str:
    from . import env

    path = shutil.which(name)
    if not path:
        raise FileNotFoundError(f"{name} not on PATH")
    flag = "-version" if name.startswith("ff") else "--version"
    out = subprocess.run([path, flag], capture_output=True, text=True, timeout=30)
    if out.returncode != 0:
        raise RuntimeError(f"{path} exited with {out.returncode}: {out.stderr.strip()[:200]}")
    first = (out.stdout or out.stderr).strip().splitlines()[0]
    bundled = env.bundled_bin_dir()
    where = "bundled" if bundled and path.startswith(bundled) else path
    return f"{first[:70]} [{where}]"


def run(online: bool = False, emit: Callable[[str], None] = print) -> bool:
    """Returns True when every required check passed."""
    from . import __version__, env

    env.ensure_tool_paths()
    r = _Report(emit)
    emit(f"melograb {__version__} · Python {sys.version.split()[0]} · {sys.platform}"
         f"{' · packaged app' if env.is_frozen() else ''}")

    def ytdlp():
        import yt_dlp
        from yt_dlp.version import __version__ as v

        return f"{v} from {'update' if '.whl' in yt_dlp.__file__ else 'bundle' if env.is_frozen() else 'install'}"

    def ejs():
        import yt_dlp_ejs.yt.solver as solver

        size = len(solver.core()) + len(solver.lib())
        if size < 1000:
            raise RuntimeError("solver scripts are empty")
        return f"{size // 1024} KB of solver code"

    def ytmusic():
        import ytmusicapi
        from ytmusicapi import YTMusic

        YTMusic()  # loads bundled locale data
        return getattr(ytmusicapi, "__version__", "")

    def tls():
        import certifi

        if not os.path.exists(certifi.where()):
            raise FileNotFoundError(certifi.where())
        return "certificates present"

    def tkinter():
        import tkinter as tk

        from PIL import Image, ImageTk

        try:
            root = tk.Tk()
        except tk.TclError:
            return f"Tk {tk.TkVersion} (no display to test drawing)"
        try:
            root.withdraw()
            ImageTk.PhotoImage(Image.new("RGBA", (4, 4)))  # needs PIL's Tk bridge
        finally:
            root.destroy()
        return f"Tk {tk.TkVersion}, artwork rendering works"

    r.check("yt-dlp", ytdlp)
    r.check("YouTube challenge solver (yt-dlp-ejs)", ejs)
    r.check("ytmusicapi", ytmusic)
    r.check("TLS certificates", tls)
    r.check("Tk (GUI)", tkinter, required=False)
    r.check("ffmpeg", lambda: _tool_version("ffmpeg"))
    r.check("ffprobe", lambda: _tool_version("ffprobe"), required=False)

    def js_runtime():
        runtimes = env.available_js_runtimes()
        if not runtimes:
            raise FileNotFoundError("no deno/node/bun/quickjs on PATH")
        return _tool_version(runtimes[0])

    r.check("JavaScript runtime", js_runtime)

    with tempfile.TemporaryDirectory(prefix="melograb-selftest-") as tmp:
        r.check("convert + tag (mp3, m4a, opus)", lambda: _convert_and_tag(tmp))
        if online:
            r.check("online: fetch album + download one song", lambda: _online(tmp))

    ok = r.failures == 0
    emit("All good." if ok else f"{r.failures} problem(s) found.")
    return ok


def _convert_and_tag(tmp: str) -> str:
    from mutagen import File

    from . import tag
    from .track import Track

    track = Track(title="Self Test", artist="melograb", album="Diagnostics", track_number=1)
    codecs = {"mp3": "libmp3lame", "m4a": "aac", "opus": "libopus"}
    for ext, codec in codecs.items():
        path = os.path.join(tmp, f"test.{ext}")
        subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
             "-c:a", codec, path],
            check=True, capture_output=True, timeout=60,
        )
        tag.tag_file(path, track)
        title = (File(path, easy=True) or {}).get("title")
        if title != ["Self Test"]:
            raise RuntimeError(f"{ext}: tags didn't stick ({title})")
    return ", ".join(codecs)


def _online(tmp: str) -> str:
    from . import pipeline, sources

    coll = sources.resolve(ONLINE_ALBUM)
    track = coll.tracks[0]
    (result,) = pipeline.run([track], tmp, pipeline.Options(fetch_lyrics=True, workers=1))
    if not result.ok:
        raise RuntimeError(f"{track.display_name}: {result.error}")
    size = os.path.getsize(result.path) // 1024
    return f"“{coll.name}” ({len(coll.tracks)} tracks) → {os.path.basename(result.path)}, {size} KB"


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    return 0 if run(online="--online" in argv) else 1
