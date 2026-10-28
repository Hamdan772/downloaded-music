"""Fetch Spotify playlist/album track lists from the PUBLIC embed page --
the same page Spotify serves to anyone (logged out, no API key) to render
a preview player. No login, no developer app, no Premium required.

This only reads metadata that Spotify already publishes to anonymous
visitors; it never touches audio streams or anything behind a login wall.
"""
from __future__ import annotations

import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

import requests

from .track import Track

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
_NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
_ID_RE = re.compile(r"^[A-Za-z0-9]{16,32}$")


def parse_spotify_url(url: str) -> tuple[str, str]:
    text = url.strip()
    if text.startswith("spotify:"):
        parts = text.split(":")
        if len(parts) == 3 and parts[1] in ("playlist", "album") and _ID_RE.match(parts[2]):
            return parts[1], parts[2]
        raise ValueError(f"Not a recognizable Spotify URI: {url}")

    parsed = urlparse(text)
    if parsed.netloc.lower() not in ("open.spotify.com", "play.spotify.com"):
        raise ValueError(f"Not a recognizable Spotify URL: {url}")
    parts = [p for p in parsed.path.split("/") if p]
    if parts and re.fullmatch(r"intl-[a-z]{2}(?:-[a-z]{2})?", parts[0], re.I):
        parts = parts[1:]
    if len(parts) < 2 or parts[0].lower() not in ("playlist", "album"):
        raise ValueError(f"Only playlist/album links are supported: {url}")
    kind, item_id = parts[0].lower(), parts[1]
    if not _ID_RE.match(item_id):
        raise ValueError(f"Could not find a valid ID in: {url}")
    return kind, item_id


def _get_entity(kind: str, item_id: str, timeout: int = 20) -> dict:
    resp = requests.get(
        f"https://open.spotify.com/embed/{kind}/{item_id}",
        headers=_HEADERS,
        timeout=timeout,
    )
    if resp.status_code == 404:
        raise ValueError("Playlist/album not found -- is it public, and is the link correct?")
    resp.raise_for_status()

    m = _NEXT_DATA_RE.search(resp.text)
    if not m:
        raise ValueError("Spotify's embed page didn't return the expected data. Try again.")
    data = json.loads(m.group(1))
    entity = data.get("props", {}).get("pageProps", {}).get("state", {}).get("data", {}).get("entity")
    if not isinstance(entity, dict):
        raise ValueError("Spotify's embed page returned no track data (private playlist?).")
    return entity


def _extract_cover(entity: dict) -> str:
    """Playlists expose coverArt.sources; albums expose visualIdentity.image.
    Different field per entity type, same shape (list of {url, width/maxWidth})."""
    cover_art = entity.get("coverArt")
    if isinstance(cover_art, dict):
        images = cover_art.get("sources") or []
        if images:
            return sorted(images, key=lambda i: i.get("width", 0))[-1]["url"]

    visual = entity.get("visualIdentity")
    if isinstance(visual, dict):
        images = visual.get("image") or []
        if images:
            return sorted(images, key=lambda i: i.get("maxWidth", 0))[-1]["url"]

    return ""


def fetch_playlist(url: str) -> tuple[str, list[Track]]:
    """Returns (playlist/album name, tracks)."""
    kind, item_id = parse_spotify_url(url)
    entity = _get_entity(kind, item_id)

    items = entity.get("trackList") or []
    album_name = entity.get("name") or entity.get("title") or ""
    cover = _extract_cover(entity)

    tracks = []
    for i, item in enumerate(items, start=1):
        title = item.get("title", "")
        artist = item.get("subtitle", "")
        if not title:
            continue
        track_id = item.get("uri", "").rsplit(":", 1)[-1] if item.get("uri") else ""
        tracks.append(
            Track(
                title=title,
                artist=artist,
                album=album_name if kind == "album" else "",
                track_number=i,
                duration_ms=item.get("duration", 0),
                cover_url=cover,
                spotify_id=track_id,
            )
        )
    if not tracks:
        raise ValueError("No tracks found -- the playlist may be empty or private.")

    if kind == "playlist":
        _enrich_track_albums(tracks)

    return album_name, tracks


_META_ALBUM_RE = re.compile(r'<meta[^>]*name="music:album"[^>]*content="([^"]*)"')
_META_TRACKNUM_RE = re.compile(r'<meta[^>]*name="music:album:track"[^>]*content="([^"]*)"')
_album_cache: dict = {}
_album_cache_lock = threading.Lock()


def _album_name_and_cover(album_id: str, timeout: int = 15) -> tuple[str, str]:
    with _album_cache_lock:
        if album_id in _album_cache:
            return _album_cache[album_id]
    try:
        entity = _get_entity("album", album_id, timeout=timeout)
        name = entity.get("name") or entity.get("title") or ""
        cover = _extract_cover(entity)
    except Exception:  # noqa: BLE001
        name, cover = "", ""
    with _album_cache_lock:
        _album_cache[album_id] = (name, cover)
    return name, cover


def _enrich_one(track: Track, timeout: int = 15) -> None:
    if not track.spotify_id:
        return
    try:
        resp = requests.get(
            f"https://open.spotify.com/track/{track.spotify_id}",
            headers=_HEADERS,
            timeout=timeout,
        )
        resp.raise_for_status()
    except requests.RequestException:
        return

    m = _META_ALBUM_RE.search(resp.text)
    if not m:
        return
    album_url = m.group(1)
    album_id = album_url.rstrip("/").rsplit("/", 1)[-1]
    if not _ID_RE.match(album_id):
        return

    name, cover = _album_name_and_cover(album_id)
    if name:
        track.album = name
    if cover and not track.cover_url:
        track.cover_url = cover

    tm = _META_TRACKNUM_RE.search(resp.text)
    if tm and tm.group(1).isdigit():
        track.track_number = int(tm.group(1))


def _enrich_track_albums(tracks: list[Track], workers: int = 8) -> None:
    """Fill in each playlist track's real album (the playlist page itself
    only gives title/artist). Fetches each track's public page, which is
    the same no-login data Spotify shows a logged-out visitor."""
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_enrich_one, t) for t in tracks]
        for f in as_completed(futures):
            f.result()


def fetch_tracks(url: str) -> list[Track]:
    _, tracks = fetch_playlist(url)
    return tracks
