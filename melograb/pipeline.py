from __future__ import annotations

import logging
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, Optional

from . import downloader, lyrics, tag, youtube_match
from .track import Track, sanitize_filename

log = logging.getLogger(__name__)

# Errors that will hit every candidate the same way -- no point retrying.
_FATAL_HINTS = ("ffmpeg is not installed",)
_THUMB_SIZE_RE = re.compile(r"=w\d+-h\d+")


@dataclass
class Result:
    track: Track
    path: Optional[str] = None
    error: Optional[str] = None
    status: str = "failed"  # done | skipped | duplicate | failed | cancelled

    @property
    def ok(self) -> bool:
        return self.status in ("done", "skipped", "duplicate")


def output_path(track: Track, out_dir: str, fmt: str, group_by_album: bool) -> str:
    dest_dir = os.path.join(out_dir, track.album_folder_name) if group_by_album else out_dir
    return os.path.join(dest_dir, f"{track.filename_stem}.{fmt}")


def _bigger_thumbnail(url: str) -> str:
    # YouTube Music search thumbnails are tiny (60-120px); the same URL
    # serves any size.
    return _THUMB_SIZE_RE.sub("=w600-h600", url)


def process_one(
    track: Track, out_dir: str, fmt: str, group_by_album: bool = True, fetch_lyrics: bool = True
) -> Result:
    try:
        path = output_path(track, out_dir, fmt, group_by_album)
        if os.path.exists(path):
            return Result(track, path, status="skipped")

        candidates = youtube_match.rank_matches(track, limit=3)
        if not candidates:
            return Result(track, error="no matching song found on YouTube Music")
        best = candidates[0]

        if not track.album and isinstance(best.get("album"), dict) and best["album"].get("name"):
            track.album = best["album"]["name"]
            path = output_path(track, out_dir, fmt, group_by_album)
            if os.path.exists(path):
                return Result(track, path, status="skipped")
        if not track.cover_url and best.get("thumbnails"):
            track.cover_url = _bigger_thumbnail(best["thumbnails"][-1]["url"])

        if fetch_lyrics:
            try:
                track.lyrics_lrc, track.lyrics_plain = lyrics.fetch_lyrics(track)
            except Exception:  # noqa: BLE001 - lyrics are best-effort
                log.exception("lyrics lookup failed for %s", track.display_name)

        # If the best match won't download (blocked, removed, region-locked),
        # the runner-up is usually the same song from another upload.
        downloaded = None
        errors = []
        for cand in candidates[:2]:
            try:
                downloaded = downloader.download_audio(
                    cand["videoId"], os.path.dirname(path), track.filename_stem, fmt
                )
                break
            except downloader.DownloadError as e:
                log.warning("download of %s (%s) failed: %s", track.display_name, cand["videoId"], e)
                errors.append(str(e))
                if any(h in str(e) for h in _FATAL_HINTS):
                    break
        if downloaded is None:
            return Result(track, error=errors[0] if errors else "download failed")
        path = downloaded

        try:
            tag.tag_file(path, track)
        except Exception:  # noqa: BLE001 - an untagged file still beats no file
            log.exception("tagging failed for %s", path)
        return Result(track, path, status="done")
    except Exception as e:  # noqa: BLE001 - surface all failures per-track
        log.exception("processing %s failed", track.display_name)
        return Result(track, error=downloader.friendly_error(str(e)) or type(e).__name__)


def run(
    tracks: list[Track],
    out_dir: str,
    fmt: str = "mp3",
    workers: int = 4,
    group_by_album: bool = True,
    fetch_lyrics: bool = True,
    on_start: Optional[Callable[[int], None]] = None,
    on_result: Optional[Callable[[int, Result], None]] = None,
    cancel: Optional[threading.Event] = None,
) -> list[Result]:
    """Process every track; returns one Result per track, in input order.
    `on_start(i)` / `on_result(i, result)` are called from worker threads."""
    os.makedirs(out_dir, exist_ok=True)
    results: list[Optional[Result]] = [None] * len(tracks)

    # The same song twice in one playlist would race for one file.
    first_seen: dict = {}
    todo = []
    for i, t in enumerate(tracks):
        key = os.path.normcase(output_path(t, out_dir, fmt, group_by_album))
        if key in first_seen:
            results[i] = Result(t, error=None, status="duplicate")
        else:
            first_seen[key] = i
            todo.append(i)

    for i, r in enumerate(results):
        if r and on_result:
            on_result(i, r)

    def work(i: int) -> None:
        if cancel.is_set():
            result = Result(tracks[i], error="cancelled", status="cancelled")
        else:
            if on_start:
                on_start(i)
            result = process_one(tracks[i], out_dir, fmt, group_by_album, fetch_lyrics)
        results[i] = result
        if on_result:
            on_result(i, result)

    if cancel is None:
        cancel = threading.Event()
    with ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="download") as pool:
        try:
            list(pool.map(work, todo))
        except KeyboardInterrupt:
            # Let in-flight tracks finish; everything still queued bails out.
            cancel.set()
            raise

    return results  # type: ignore[return-value]


def write_m3u(results: list[Result], out_dir: str, name: str) -> Optional[str]:
    """Write `<out_dir>/<name>.m3u8` listing the downloaded tracks in source
    order, with paths relative to it. Returns its path, or None if empty."""
    entries = [r for r in results if r.path and os.path.exists(r.path)]
    if not entries:
        return None
    m3u_path = os.path.join(out_dir, f"{sanitize_filename(name, fallback='playlist')}.m3u8")
    lines = ["#EXTM3U"]
    for r in entries:
        seconds = round(r.track.duration_ms / 1000) if r.track.duration_ms else -1
        lines.append(f"#EXTINF:{seconds},{r.track.display_name}")
        lines.append(os.path.relpath(r.path, out_dir).replace(os.sep, "/"))
    with open(m3u_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return m3u_path


def summarize_failures(results: list[Result]) -> list[tuple[str, int]]:
    """[(reason, count)] most common first -- so one systemic problem (e.g.
    an outdated yt-dlp) is obvious instead of buried in N red rows."""
    counts: dict = {}
    for r in results:
        if r.status == "failed" and r.error:
            counts[r.error] = counts.get(r.error, 0) + 1
    return sorted(counts.items(), key=lambda p: p[1], reverse=True)
