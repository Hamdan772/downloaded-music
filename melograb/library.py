"""Work on music that's already on disk -- currently: add lyrics to every
song in a folder (from any source, not just melograb downloads)."""
from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional

from . import lyrics, tag
from .track import Track

log = logging.getLogger(__name__)

AUDIO_EXTS = (".mp3", ".m4a", ".opus", ".ogg", ".flac")


def iter_audio_files(root: str):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for name in sorted(filenames):
            if name.lower().endswith(AUDIO_EXTS) and not name.startswith("."):
                yield os.path.join(dirpath, name)


def read_track(path: str) -> Optional[Track]:
    """Title/artist/album/duration from a file's tags, or None if it has no
    title+artist to search lyrics with."""
    from mutagen import File

    try:
        audio = File(path, easy=True)
    except Exception:  # noqa: BLE001
        return None
    if audio is None or not audio.tags:
        return None

    def first(key: str) -> str:
        values = audio.tags.get(key) or []
        return str(values[0]).strip() if values else ""

    title, artist = first("title"), first("artist")
    if not title or not artist:
        return None
    length = getattr(audio.info, "length", 0) or 0
    return Track(title=title, artist=artist, album=first("album"), duration_ms=int(length * 1000))


def add_lyrics_to_file(path: str, overwrite: bool = False) -> str:
    """Returns what happened: 'synced', 'plain', 'has lyrics', 'not found',
    'no tags' or 'error: ...'."""
    try:
        if not overwrite and tag.has_lyrics(path):
            return "has lyrics"
        track = read_track(path)
        if track is None:
            return "no tags"
        synced, plain = lyrics.fetch_lyrics(track)
        if not (synced or plain):
            return "not found"
        tag.embed_lyrics(path, synced, plain)
        return "synced" if synced else "plain"
    except Exception as e:  # noqa: BLE001
        log.exception("adding lyrics to %s failed", path)
        return f"error: {e}"


def add_lyrics_to_folder(
    root: str,
    overwrite: bool = False,
    workers: int = 4,
    on_result: Optional[Callable[[str, str], None]] = None,
) -> dict:
    """Returns {path: outcome}. `on_result(path, outcome)` runs per file
    (from worker threads)."""
    files = list(iter_audio_files(root))
    results = {}

    def work(path: str) -> None:
        outcome = add_lyrics_to_file(path, overwrite)
        results[path] = outcome
        if on_result:
            on_result(path, outcome)

    with ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="lyrics") as pool:
        list(pool.map(work, files))
    return results
