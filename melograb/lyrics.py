"""Fetch time-stamped (LRC) lyrics from lrclib.net -- a free, public,
no-login, no-API-key lyrics database used by several open-source players.
"""
from __future__ import annotations

import requests

from .track import Track

_BASE = "https://lrclib.net/api"
_HEADERS = {"User-Agent": "melograb (https://github.com/) - personal use"}


def fetch_lrc(track: Track, timeout: int = 10) -> str:
    """Returns LRC-formatted synced lyrics, or '' if none found."""
    duration_s = round(track.duration_ms / 1000) if track.duration_ms else None

    params = {"track_name": track.title, "artist_name": track.artist}
    if track.album:
        params["album_name"] = track.album
    if duration_s:
        params["duration"] = duration_s

    try:
        resp = requests.get(f"{_BASE}/get", params=params, headers=_HEADERS, timeout=timeout)
        if resp.status_code == 200:
            data = resp.json()
            lrc = data.get("syncedLyrics")
            if lrc:
                return lrc
    except requests.RequestException:
        pass

    # Fall back to a fuzzy search if the exact lookup missed (wrong album
    # name/duration rounding are common causes).
    try:
        resp = requests.get(
            f"{_BASE}/search",
            params={"track_name": track.title, "artist_name": track.artist},
            headers=_HEADERS,
            timeout=timeout,
        )
        if resp.status_code == 200:
            results = resp.json()
            for item in results:
                if item.get("syncedLyrics"):
                    return item["syncedLyrics"]
    except requests.RequestException:
        pass

    return ""
