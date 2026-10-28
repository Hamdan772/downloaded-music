"""Official album info and artwork from Apple's public iTunes Search API
(free, no key, no login).

Fills in what the source didn't provide -- the album's own cover art in high
resolution, album name and album artist, track and disc numbers, genre and
release date -- without overwriting anything the source already got right.
Spotify (when its per-song lookups work) stays the authority; this is the
safety net for when they don't, and for YouTube/CSV/search sources.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from collections import deque
from typing import Optional

import requests
from rapidfuzz import fuzz

from .track import Track
from .youtube_match import _norm, _strip_feat

log = logging.getLogger(__name__)

API = "https://itunes.apple.com/search"
ARTWORK_SIZE = 1200
MIN_TITLE, MIN_ARTIST, MIN_ALBUM = 80, 70, 85
MAX_DURATION_DIFF_S = 6
_HEADERS = {"User-Agent": "melograb (personal music library tool)"}
_RELEASE_SUFFIX_RE = re.compile(r"\s+-\s+(single|ep)$", re.I)
_ARTWORK_SIZE_RE = re.compile(r"/\d+x\d+(bb|cc|-\d+)?\.(jpg|jpeg|png|webp)$", re.I)


class _RateLimiter:
    """At most `calls` requests per `period` seconds across all threads
    (Apple allows roughly 20 searches a minute per IP)."""

    def __init__(self, calls: int, period: float):
        self.calls, self.period = calls, period
        self.stamps: deque = deque()
        self.lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self.lock:
                now = time.monotonic()
                while self.stamps and now - self.stamps[0] >= self.period:
                    self.stamps.popleft()
                if len(self.stamps) < self.calls:
                    self.stamps.append(now)
                    return
                wait = self.period - (now - self.stamps[0])
            time.sleep(max(wait, 0.05))


_limiter = _RateLimiter(20, 60.0)
_cache: dict = {}
_cache_lock = threading.Lock()


def artwork_url(url: str, size: int = ARTWORK_SIZE) -> str:
    """Apple serves the same artwork at any size via the file name
    (…/100x100bb.jpg -> …/1200x1200bb.jpg)."""
    return _ARTWORK_SIZE_RE.sub(f"/{size}x{size}bb.jpg", url) if url else ""


def clean_album_name(name: str) -> str:
    """'Song - Single' / 'Thing - EP' -> 'Song' / 'Thing' (Spotify's naming)."""
    return _RELEASE_SUFFIX_RE.sub("", (name or "").strip())


def _search(params: dict, timeout: int = 10) -> list:
    key = tuple(sorted(params.items()))
    with _cache_lock:
        if key in _cache:
            return _cache[key]
    _limiter.acquire()
    try:
        resp = requests.get(API, params=params, headers=_HEADERS, timeout=timeout)
        if resp.status_code != 200:
            log.info("Apple Music search %s -> HTTP %s", params.get("term"), resp.status_code)
            return []  # not cached: rate limits and outages are temporary
        results = resp.json().get("results") or []
    except (requests.RequestException, ValueError) as e:
        log.info("Apple Music search failed: %s", e)
        return []
    with _cache_lock:
        _cache[key] = results
    return results


def _first_artist(artist: str) -> str:
    return (artist or "").split(",")[0].strip()


def _artist_score(track_artist: str, candidate: str) -> float:
    return max(
        fuzz.token_set_ratio(_norm(track_artist), _norm(candidate)),
        fuzz.token_set_ratio(_norm(_first_artist(track_artist)), _norm(candidate)),
    )


def _title_score(title: str, candidate: str) -> float:
    return max(
        fuzz.token_sort_ratio(_norm(title), _norm(candidate)),
        fuzz.token_sort_ratio(_norm(_strip_feat(title)), _norm(_strip_feat(candidate))),
    )


def _album_score(album: str, candidate: str) -> float:
    return fuzz.token_sort_ratio(_norm(clean_album_name(album)), _norm(clean_album_name(candidate)))


def find_song(track: Track, country: str = "US") -> Optional[dict]:
    """Best matching Apple Music song result for `track`, or None."""
    if not track.title or not track.artist:
        return None
    term = f"{_first_artist(track.artist)} {_strip_feat(track.title)}"
    results = _search({"term": term, "media": "music", "entity": "song", "limit": 15, "country": country})
    best, best_score = None, 0.0
    for r in results:
        if r.get("wrapperType") not in (None, "track") or r.get("kind") not in (None, "song"):
            continue
        title_s = _title_score(track.title, r.get("trackName", ""))
        artist_s = _artist_score(track.artist, r.get("artistName", ""))
        if title_s < MIN_TITLE or artist_s < MIN_ARTIST:
            continue
        if track.duration_ms and r.get("trackTimeMillis"):
            if abs(track.duration_ms - r["trackTimeMillis"]) / 1000 > MAX_DURATION_DIFF_S:
                continue
        score = title_s * 0.5 + artist_s * 0.3
        score += 0.2 * (_album_score(track.album, r.get("collectionName", "")) if track.album else 50)
        # Prefer the original release over compilations/"Greatest Hits".
        if r.get("collectionArtistName", "").lower() == "various artists":
            score -= 10
        if score > best_score:
            best, best_score = r, score
    return best


def find_album(artist: str, album: str, country: str = "US") -> Optional[dict]:
    """Best matching Apple Music album (collection) result, or None."""
    if not artist or not album:
        return None
    term = f"{_first_artist(artist)} {clean_album_name(album)}"
    results = _search({"term": term, "media": "music", "entity": "album", "limit": 10, "country": country})
    best, best_score = None, 0.0
    for r in results:
        album_s = _album_score(album, r.get("collectionName", ""))
        artist_s = _artist_score(artist, r.get("artistName", ""))
        if album_s < MIN_ALBUM or artist_s < MIN_ARTIST:
            continue
        score = album_s * 0.6 + artist_s * 0.4
        if score > best_score:
            best, best_score = r, score
    return best


def _apply_album_fields(track: Track, info: dict) -> None:
    if info.get("artworkUrl100") and not track.cover_is_official:
        track.cover_url = artwork_url(info["artworkUrl100"])
        track.cover_is_official = True
    if not track.genre and info.get("primaryGenreName"):
        track.genre = info["primaryGenreName"]
    if not track.release_date and info.get("releaseDate"):
        track.release_date = str(info["releaseDate"])[:10]
    if not track.album_artist:
        track.album_artist = info.get("collectionArtistName") or info.get("artistName") or ""
    if not track.total_tracks and info.get("trackCount"):
        track.total_tracks = int(info["trackCount"])


def enrich(track: Track, country: str = "US") -> bool:
    """Fill missing album info/artwork from Apple Music. Returns True if a
    match was found. Never raises for network problems."""
    if not track.album or not track.track_number:
        song = find_song(track, country)
        if not song:
            return False
        same_album = not track.album or _album_score(track.album, song.get("collectionName", "")) >= MIN_ALBUM
        if not same_album:
            # A different release of the song (single vs album...): only
            # take release-independent info.
            if not track.genre and song.get("primaryGenreName"):
                track.genre = song["primaryGenreName"]
            return True
        if not track.album:
            track.album = clean_album_name(song.get("collectionName", ""))
        if not track.track_number and song.get("trackNumber"):
            track.track_number = int(song["trackNumber"])
            track.total_tracks = track.total_tracks or int(song.get("trackCount") or 0)
        if not track.disc_number and song.get("discNumber") and (song.get("discCount") or 1) > 1:
            track.disc_number = int(song["discNumber"])
            track.total_discs = int(song.get("discCount") or 0)
        if song.get("trackExplicitness") == "explicit":
            track.explicit = True
        _apply_album_fields(track, song)
        return True

    info = find_album(track.album_artist or track.artist, track.album, country)
    if not info:
        return False
    _apply_album_fields(track, info)
    return True
