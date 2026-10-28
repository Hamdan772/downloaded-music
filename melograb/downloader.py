from __future__ import annotations

import logging
import os
import re
import shutil
import tempfile

import yt_dlp

from . import env

log = logging.getLogger(__name__)

FORMATS = ("mp3", "m4a", "opus")
# "best" = highest-quality VBR when transcoding; numbers are kbps. When the
# source is already in the target codec it's copied untouched either way.
QUALITIES = ("best", "320", "256", "192", "128")
# Browsers yt-dlp can borrow a YouTube login from, for when YouTube asks to
# "confirm you're not a bot".
BROWSERS = ("safari", "chrome", "firefox", "brave", "edge", "arc", "vivaldi", "opera", "chromium")
SIGN_IN_HINT = "pick the browser you use YouTube in under “YouTube sign-in” (CLI: --cookies-from-browser)"

# Prefer a source stream that's already in the target codec so m4a/opus
# don't get needlessly re-encoded.
_FORMAT_SELECTOR = {
    "mp3": "bestaudio/best",
    "m4a": "bestaudio[ext=m4a]/bestaudio[acodec^=mp4a]/bestaudio/best",
    "opus": "bestaudio[acodec=opus]/bestaudio/best",
}

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_UPDATE_HINT = 'update yt-dlp (`pip install -U "yt-dlp[default]"`) and install deno (`brew install deno`)'
_ERROR_HINTS = (
    ("ffprobe and ffmpeg not found", "ffmpeg is not installed (macOS: `brew install ffmpeg`)"),
    ("ffmpeg not found", "ffmpeg is not installed (macOS: `brew install ffmpeg`)"),
    ("confirm your age", "video is age-restricted"),
    ("operation not permitted", "macOS blocked access to the browser's cookies -- give melograb Full Disk Access "
     "(System Settings › Privacy & Security) or choose a different browser"),
    ("cookies database", "couldn't find that browser's cookies -- is it installed, and have you opened YouTube in it?"),
    ("sign in to confirm", f"YouTube is asking for bot verification -- {SIGN_IN_HINT}, or wait a while and retry"),
    ("requested format is not available", f"YouTube returned no downloadable audio -- {_UPDATE_HINT}"),
    ("only images are available", f"YouTube returned no downloadable audio -- {_UPDATE_HINT}"),
    ("challenge solving failed", f"couldn't solve YouTube's player challenge -- {_UPDATE_HINT}"),
    ("http error 403", f"YouTube refused the download (403) -- {_UPDATE_HINT}"),
    ("video unavailable", "video is unavailable"),
    ("private video", "video is private"),
    ("not available in your country", "video is region-blocked"),
)


class DownloadError(Exception):
    pass


def friendly_error(message: str) -> str:
    message = _ANSI_RE.sub("", str(message)).replace("ERROR: ", "").strip()
    lower = message.lower()
    for needle, hint in _ERROR_HINTS:
        if needle in lower:
            return hint
    return message.splitlines()[0] if message else "unknown error"


class _YtdlpLogger:
    """Route yt-dlp's chatter into our log file instead of stdout."""

    def debug(self, msg):
        if not msg.startswith("[debug] "):
            log.debug(msg)

    def info(self, msg):
        log.debug(msg)

    def warning(self, msg):
        log.warning(_ANSI_RE.sub("", msg))

    def error(self, msg):
        log.error(_ANSI_RE.sub("", msg))


def _ydl_opts(
    tmp_dir: str, fmt: str, quality: str = "best", remove_non_music: bool = False, cookies_browser: str = ""
) -> dict:
    postprocessors = []
    if remove_non_music:
        # Cut intros/skits/outros that SponsorBlock users marked as
        # non-music (only meaningful for music videos, not audio tracks).
        postprocessors.append({"key": "SponsorBlock", "categories": ["music_offtopic"], "when": "after_filter"})
    postprocessors.append(
        {"key": "FFmpegExtractAudio", "preferredcodec": fmt, "preferredquality": "0" if quality == "best" else quality}
    )
    if remove_non_music:
        postprocessors.append({"key": "ModifyChapters", "remove_sponsor_segments": ["music_offtopic"]})
    opts = {
        "format": _FORMAT_SELECTOR[fmt],
        "outtmpl": os.path.join(tmp_dir, "%(id)s.%(ext)s"),
        "postprocessors": postprocessors,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": False,
        "noprogress": True,
        "no_color": True,
        "logger": _YtdlpLogger(),
        "retries": 5,
        "fragment_retries": 5,
        "socket_timeout": 30,
        "overwrites": True,
    }
    # Let yt-dlp choose its own YouTube player clients -- they change often
    # and its defaults track what currently works. Just tell it about every
    # JS runtime we can find (it only looks for deno by default).
    runtimes = env.available_js_runtimes()
    if runtimes:
        opts["js_runtimes"] = {name: {} for name in runtimes}
    if cookies_browser:
        # Use the YouTube login from this browser (yt-dlp reads its cookies
        # locally; nothing is sent anywhere but YouTube).
        opts["cookiesfrombrowser"] = (cookies_browser,)
    return opts


def download_audio(
    video_id: str,
    out_dir: str,
    filename_stem: str,
    fmt: str = "mp3",
    quality: str = "best",
    remove_non_music: bool = False,
    cookies_browser: str = "",
) -> str:
    """Download `video_id` as `<out_dir>/<filename_stem>.<fmt>`; returns the path.
    Raises DownloadError with a human-readable reason on failure."""
    if fmt not in FORMATS:
        raise ValueError(f"Unsupported format: {fmt}")
    if quality not in QUALITIES:
        raise ValueError(f"Unsupported quality: {quality}")
    if cookies_browser and cookies_browser not in BROWSERS:
        raise ValueError(f"Unsupported browser: {cookies_browser}")

    os.makedirs(out_dir, exist_ok=True)
    final_path = os.path.join(out_dir, f"{filename_stem}.{fmt}")
    # Work in a private temp dir so partial downloads never land in the
    # library and odd characters in song names never reach yt-dlp's
    # output-template parser.
    tmp_dir = tempfile.mkdtemp(prefix=".melograb-", dir=out_dir)
    try:
        url = f"https://www.youtube.com/watch?v={video_id}"
        try:
            try:
                with yt_dlp.YoutubeDL(_ydl_opts(tmp_dir, fmt, quality, remove_non_music, cookies_browser)) as ydl:
                    ydl.download([url])
            except yt_dlp.utils.DownloadError as e:
                # SponsorBlock is a nice-to-have; its API being down mustn't
                # cost us the track.
                if not (remove_non_music and "sponsorblock" in str(e).lower()):
                    raise
                log.warning("SponsorBlock unavailable for %s, downloading uncut: %s", video_id, e)
                for f in os.listdir(tmp_dir):
                    os.remove(os.path.join(tmp_dir, f))
                with yt_dlp.YoutubeDL(_ydl_opts(tmp_dir, fmt, quality, False, cookies_browser)) as ydl:
                    ydl.download([url])
        except yt_dlp.utils.DownloadError as e:
            raise DownloadError(friendly_error(str(e))) from e

        produced = [f for f in os.listdir(tmp_dir) if f.endswith(f".{fmt}")]
        if not produced:
            leftovers = ", ".join(sorted(os.listdir(tmp_dir))) or "nothing"
            raise DownloadError(f"conversion to {fmt} failed (got: {leftovers}) -- is ffmpeg installed?")
        os.replace(os.path.join(tmp_dir, produced[0]), final_path)
        return final_path
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
