from __future__ import annotations

import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, Optional

from . import downloader, env, lyrics, tag, youtube_match
from . import template as templates
from .track import Track, sanitize_filename

log = logging.getLogger(__name__)

# Errors that will hit every candidate the same way -- no point retrying.
_FATAL_HINTS = ("ffmpeg is not installed", "bot verification", "browser's cookies", "that browser's cookies")


@dataclass
class Options:
    fmt: str = "mp3"
    template: str = templates.DEFAULT_TEMPLATE
    fetch_lyrics: bool = True
    quality: str = "best"
    # Cut non-music intros/outros from music-video matches via SponsorBlock.
    remove_non_music: bool = True
    workers: int = 4
    # Borrow this browser's YouTube login when YouTube demands a bot check.
    cookies_browser: str = ""

    def validate(self) -> None:
        if self.fmt not in downloader.FORMATS:
            raise ValueError(f"unsupported format {self.fmt!r}")
        if self.quality not in downloader.QUALITIES:
            raise ValueError(f"unsupported quality {self.quality!r}")
        if self.cookies_browser and self.cookies_browser not in downloader.BROWSERS:
            raise ValueError(f"unsupported browser {self.cookies_browser!r}")
        templates.validate(self.template)


@dataclass
class Result:
    track: Track
    path: Optional[str] = None
    error: Optional[str] = None
    status: str = "failed"  # done | skipped | duplicate | failed | cancelled
    video_id: str = ""
    match_title: str = ""

    @property
    def ok(self) -> bool:
        return self.status in ("done", "skipped", "duplicate")


def output_path(track: Track, out_dir: str, options: Options) -> str:
    return os.path.join(out_dir, f"{templates.render(options.template, track)}.{options.fmt}")


def _candidate_title(cand: dict) -> str:
    artists = ", ".join(a.get("name", "") for a in cand.get("artists") or [] if a)
    title = cand.get("title") or ""
    return f"{artists} - {title}" if artists and title else title


def find_candidates(track: Track, limit: int = 3) -> list[dict]:
    """The video(s) to try for `track`: its known video, else search results."""
    if track.video_id:
        return [{
            "videoId": track.video_id,
            "resultType": "song" if track.video_type == "song" else "video",
            "title": track.title,
            "artists": [{"name": track.artist}] if track.artist else [],
        }]
    return youtube_match.rank_matches(track, limit=limit)


def process_one(track: Track, out_dir: str, options: Options) -> Result:
    try:
        path = output_path(track, out_dir, options)
        if os.path.exists(path) and not track.replace_existing:
            return Result(track, path, status="skipped")

        candidates = find_candidates(track)
        if not candidates:
            return Result(track, error="no matching song found on YouTube Music")
        best = candidates[0]

        if not track.album and isinstance(best.get("album"), dict) and best["album"].get("name"):
            track.album = best["album"]["name"]
            path = output_path(track, out_dir, options)
            if os.path.exists(path) and not track.replace_existing:
                return Result(track, path, status="skipped")
        if not track.cover_url and best.get("thumbnails"):
            track.cover_url = youtube_match.best_thumbnail(best["thumbnails"])

        if options.fetch_lyrics:
            try:
                track.lyrics_lrc, track.lyrics_plain = lyrics.fetch_lyrics(track)
            except Exception:  # noqa: BLE001 - lyrics are best-effort
                log.exception("lyrics lookup failed for %s", track.display_name)

        # If the best match won't download (blocked, removed, region-locked),
        # the runner-up is usually the same song from another upload.
        dest_dir, filename = os.path.split(path)
        stem = os.path.splitext(filename)[0]
        downloaded = None
        used = best
        errors = []
        cutting = options.remove_non_music and env.has_ffprobe()
        for cand in candidates[:2]:
            try:
                downloaded = downloader.download_audio(
                    cand["videoId"],
                    dest_dir,
                    stem,
                    options.fmt,
                    quality=options.quality,
                    remove_non_music=cutting and cand.get("resultType") != "song",
                    cookies_browser=options.cookies_browser,
                )
                used = cand
                break
            except downloader.DownloadError as e:
                log.warning("download of %s (%s) failed: %s", track.display_name, cand["videoId"], e)
                errors.append(str(e))
                if any(h in str(e) for h in _FATAL_HINTS):
                    break
        if downloaded is None:
            return Result(track, error=errors[0] if errors else "download failed", video_id=best["videoId"],
                          match_title=_candidate_title(best))

        # Remember the video so the GUI can link to it (and re-runs reuse it).
        track.video_id = used["videoId"]
        track.video_type = "song" if used.get("resultType") == "song" else "video"
        try:
            tag.tag_file(downloaded, track)
        except Exception:  # noqa: BLE001 - an untagged file still beats no file
            log.exception("tagging failed for %s", downloaded)
        return Result(track, downloaded, status="done", video_id=used["videoId"], match_title=_candidate_title(used))
    except Exception as e:  # noqa: BLE001 - surface all failures per-track
        log.exception("processing %s failed", track.display_name)
        return Result(track, error=downloader.friendly_error(str(e)) or type(e).__name__)


def run(
    tracks: list[Track],
    out_dir: str,
    options: Optional[Options] = None,
    on_start: Optional[Callable[[int], None]] = None,
    on_result: Optional[Callable[[int, Result], None]] = None,
    cancel: Optional[threading.Event] = None,
) -> list[Result]:
    """Process every track; returns one Result per track, in input order.
    `on_start(i)` / `on_result(i, result)` are called from worker threads."""
    options = options or Options()
    options.validate()
    os.makedirs(out_dir, exist_ok=True)
    results: list[Optional[Result]] = [None] * len(tracks)

    # The same song twice in one playlist would race for one file.
    first_seen: dict = {}
    todo = []
    for i, t in enumerate(tracks):
        key = os.path.normcase(output_path(t, out_dir, options))
        if key in first_seen:
            results[i] = Result(t, error=None, status="duplicate")
        else:
            first_seen[key] = i
            todo.append(i)

    for i, r in enumerate(results):
        if r and on_result:
            on_result(i, r)

    if cancel is None:
        cancel = threading.Event()

    def work(i: int) -> None:
        if cancel.is_set():
            result = Result(tracks[i], error="cancelled", status="cancelled")
        else:
            if on_start:
                on_start(i)
            result = process_one(tracks[i], out_dir, options)
        results[i] = result
        if on_result:
            on_result(i, result)

    with ThreadPoolExecutor(max_workers=max(1, options.workers), thread_name_prefix="download") as pool:
        try:
            list(pool.map(work, todo))
        except KeyboardInterrupt:
            # Let in-flight tracks finish; everything still queued bails out.
            cancel.set()
            raise

    return results  # type: ignore[return-value]


def preview(tracks: list[Track], workers: int = 4) -> list[Optional[dict]]:
    """Best candidate (or None) per track, without downloading -- for
    checking matches before committing to a big download."""

    def best(track: Track) -> Optional[dict]:
        try:
            cands = find_candidates(track, limit=1)
        except Exception as e:  # noqa: BLE001
            log.warning("matching %s failed: %s", track.display_name, e)
            return None
        if not cands:
            return None
        return dict(cands[0], display=_candidate_title(cands[0]))

    with ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="match") as pool:
        return list(pool.map(best, tracks))


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
