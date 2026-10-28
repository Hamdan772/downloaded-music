"""Find the best-matching YouTube Music audio for a given Track."""
from __future__ import annotations

from rapidfuzz import fuzz
from ytmusicapi import YTMusic

from .track import Track

_ytmusic = None


def _client() -> YTMusic:
    global _ytmusic
    if _ytmusic is None:
        _ytmusic = YTMusic()
    return _ytmusic


def _score(track: Track, result: dict) -> float:
    candidate_title = result.get("title", "")
    candidate_artists = ", ".join(a["name"] for a in result.get("artists", []) or [])

    title_score = fuzz.token_sort_ratio(track.title.lower(), candidate_title.lower())
    artist_score = fuzz.token_sort_ratio(track.artist.lower(), candidate_artists.lower())
    score = title_score * 0.6 + artist_score * 0.4

    duration_s = result.get("duration_seconds")
    if duration_s and track.duration_ms:
        diff = abs(duration_s - track.duration_ms / 1000)
        if diff > 15:
            score -= min(diff - 15, 30) * 1.5

    return score


def find_best_match(track: Track) -> dict | None:
    yt = _client()
    results = yt.search(track.search_query, filter="songs", limit=8)
    if not results:
        results = yt.search(track.search_query, filter="videos", limit=8)
    if not results:
        return None

    best = max(results, key=lambda r: _score(track, r))
    return best
