"""Fetch time-stamped (LRC) lyrics from lrclib.net -- a free, public,
no-login, no-API-key lyrics database used by several open-source players.
"""
from __future__ import annotations

import logging

import requests

from . import __version__
from .track import Track

log = logging.getLogger(__name__)

_BASE = "https://lrclib.net/api"
_HEADERS = {"User-Agent": f"melograb/{__version__} (personal music library tool)"}
# lrclib durations are rounded seconds; anything further off is likely a
# different edit of the song with misaligned timestamps.
_MAX_DURATION_DIFF_S = 4


def _get(path: str, params: dict, timeout: int):
    try:
        resp = requests.get(f"{_BASE}/{path}", params=params, headers=_HEADERS, timeout=timeout)
    except requests.RequestException as e:
        log.info("lrclib %s failed: %s", path, e)
        return None
    if resp.status_code != 200:
        return None
    try:
        return resp.json()
    except ValueError:
        return None


def fetch_lyrics(track: Track, timeout: int = 10) -> tuple[str, str]:
    """Returns (synced_lrc, plain_text); either may be ''."""
    duration_s = round(track.duration_ms / 1000) if track.duration_ms else None

    params = {"track_name": track.title, "artist_name": track.artist}
    if track.album:
        params["album_name"] = track.album
    if duration_s:
        params["duration"] = duration_s

    data = _get("get", params, timeout)
    if isinstance(data, dict) and (data.get("syncedLyrics") or data.get("plainLyrics")):
        return data.get("syncedLyrics") or "", data.get("plainLyrics") or ""

    # Fall back to a fuzzy search if the exact lookup missed (wrong album
    # name/duration rounding are common causes). Prefer synced results whose
    # duration is closest to ours.
    results = _get("search", {"track_name": track.title, "artist_name": track.artist}, timeout)
    if not isinstance(results, list):
        return "", ""

    def distance(item: dict) -> float:
        if not duration_s or not item.get("duration"):
            return 0
        return abs(float(item["duration"]) - duration_s)

    candidates = [r for r in results if isinstance(r, dict) and distance(r) <= _MAX_DURATION_DIFF_S]
    candidates.sort(key=lambda r: (not r.get("syncedLyrics"), distance(r)))
    for item in candidates:
        if item.get("syncedLyrics") or item.get("plainLyrics"):
            return item.get("syncedLyrics") or "", item.get("plainLyrics") or ""
    return "", ""


def fetch_lrc(track: Track, timeout: int = 10) -> str:
    """Returns LRC-formatted synced lyrics, or '' if none found."""
    return fetch_lyrics(track, timeout)[0]
