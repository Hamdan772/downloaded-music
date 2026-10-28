"""Fetch Spotify playlist/album track lists from the PUBLIC embed page --
the same page Spotify serves to anyone (logged out, no API key) to render
a preview player. No login, no developer app, no Premium required.

This only reads metadata that Spotify already publishes to anonymous
visitors; it never touches audio streams or anything behind a login wall.
"""
from __future__ import annotations

import html
import json
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Optional
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .track import Track

log = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
_NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
_ID_RE = re.compile(r"^[A-Za-z0-9]{16,32}$")
_META_TAG_RE = re.compile(r"<meta\s[^>]*>", re.I)
_META_ATTR_RE = re.compile(r'([a-zA-Z:-]+)\s*=\s*("([^"]*)"|\'([^\']*)\')')

_session_lock = threading.Lock()
_session: Optional[requests.Session] = None


def _http() -> requests.Session:
    """Shared session: connection pooling + polite retries on 429/5xx, which
    Spotify returns when a big playlist is enriched quickly."""
    global _session
    with _session_lock:
        if _session is None:
            # Bounded backoff (1+2+4s) rather than honoring Retry-After,
            # which can ask for minutes -- enrichment is best-effort anyway.
            retry = Retry(
                total=3,
                backoff_factor=1.0,
                status_forcelist=(429, 500, 502, 503, 504),
                allowed_methods=("GET",),
                respect_retry_after_header=False,
            )
            session = requests.Session()
            adapter = HTTPAdapter(max_retries=retry, pool_maxsize=16)
            session.mount("https://", adapter)
            session.mount("http://", adapter)
            session.headers.update(_HEADERS)
            _session = session
        return _session


@dataclass
class Collection:
    kind: str  # "playlist" or "album"
    name: str
    cover_url: str = ""
    tracks: list = field(default_factory=list)


def parse_spotify_url(url: str) -> tuple[str, str]:
    text = url.strip()
    if text.startswith("spotify:"):
        parts = text.split(":")
        if len(parts) == 3 and parts[1] in ("playlist", "album") and _ID_RE.match(parts[2]):
            return parts[1], parts[2]
        raise ValueError(f"Not a recognizable Spotify URI: {url}")

    if "://" not in text:
        text = "https://" + text
    parsed = urlparse(text)
    if parsed.netloc.lower() not in ("open.spotify.com", "play.spotify.com"):
        raise ValueError(f"Not a recognizable Spotify URL: {url}")
    parts = [p for p in parsed.path.split("/") if p]
    if parts and re.fullmatch(r"intl-[a-z]{2}(?:-[a-z]{2})?", parts[0], re.I):
        parts = parts[1:]
    if parts and parts[0].lower() == "embed":
        parts = parts[1:]
    if len(parts) < 2 or parts[0].lower() not in ("playlist", "album"):
        raise ValueError(f"Only playlist/album links are supported: {url}")
    kind, item_id = parts[0].lower(), parts[1]
    if not _ID_RE.match(item_id):
        raise ValueError(f"Could not find a valid ID in: {url}")
    return kind, item_id


def _get_entity(kind: str, item_id: str, timeout: int = 20) -> dict:
    try:
        resp = _http().get(f"https://open.spotify.com/embed/{kind}/{item_id}", timeout=timeout)
    except requests.RequestException as e:
        raise ValueError(f"Couldn't reach Spotify: {e}") from e
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


def _largest_image(images, size_key: str) -> str:
    images = [i for i in images or [] if isinstance(i, dict) and i.get("url")]
    if not images:
        return ""
    return max(images, key=lambda i: i.get(size_key) or 0)["url"]


def _extract_cover(entity: dict) -> str:
    """Playlists expose coverArt.sources; albums expose visualIdentity.image.
    Different field per entity type, same shape (list of {url, width/maxWidth})."""
    cover_art = entity.get("coverArt")
    if isinstance(cover_art, dict):
        url = _largest_image(cover_art.get("sources"), "width")
        if url:
            return url

    visual = entity.get("visualIdentity")
    if isinstance(visual, dict):
        url = _largest_image(visual.get("image"), "maxWidth")
        if url:
            return url

    return ""


def _artist_text(entity: dict) -> str:
    subtitle = entity.get("subtitle")
    if isinstance(subtitle, str) and subtitle.strip():
        return subtitle.strip()
    artists = entity.get("artists")
    if isinstance(artists, list):
        names = [a.get("name", "") for a in artists if isinstance(a, dict)]
        return ", ".join(n for n in names if n)
    return ""


def fetch_collection(
    url: str, progress: Optional[Callable[[int, int], None]] = None
) -> Collection:
    """Fetch a public playlist/album. For playlists, each track is enriched
    with its real album, track number and cover. `progress(done, total)` is
    called during enrichment."""
    kind, item_id = parse_spotify_url(url)
    entity = _get_entity(kind, item_id)

    items = entity.get("trackList") or []
    name = entity.get("name") or entity.get("title") or ""
    cover = _extract_cover(entity)
    album_artist = _artist_text(entity) if kind == "album" else ""
    release_date = ""
    if kind == "album":
        rd = entity.get("releaseDate")
        release_date = (rd.get("isoString") if isinstance(rd, dict) else rd) or ""
        release_date = str(release_date)[:10]

    tracks = []
    for i, item in enumerate(items, start=1):
        title = (item.get("title") or "").strip()
        if not title:
            continue
        uri = item.get("uri") or ""
        tracks.append(
            Track(
                title=title,
                artist=(item.get("subtitle") or "").strip(),
                album=name if kind == "album" else "",
                album_artist=album_artist,
                track_number=i if kind == "album" else 0,
                total_tracks=len(items) if kind == "album" else 0,
                release_date=release_date,
                position=i,
                duration_ms=item.get("duration") or 0,
                # A playlist's own cover is only a last-resort fallback for its
                # tracks; enrichment fills in each track's real album cover.
                cover_url=cover if kind == "album" else "",
                spotify_id=uri.rsplit(":", 1)[-1] if uri else "",
            )
        )
    if not tracks:
        raise ValueError("No tracks found -- the playlist may be empty or private.")

    if kind == "playlist":
        _enrich_track_albums(tracks, progress=progress)
        for t in tracks:
            if not t.cover_url:
                t.cover_url = cover

    return Collection(kind=kind, name=name, cover_url=cover, tracks=tracks)


def fetch_playlist(url: str) -> tuple[str, list[Track]]:
    """Returns (playlist/album name, tracks)."""
    collection = fetch_collection(url)
    return collection.name, collection.tracks


def _parse_meta(page: str) -> dict:
    """{name-or-property: content} for every <meta> tag, attribute order and
    quoting agnostic. First occurrence wins."""
    meta = {}
    for tag in _META_TAG_RE.findall(page):
        attrs = {}
        for key, _, dq, sq in _META_ATTR_RE.findall(tag):
            attrs[key.lower()] = html.unescape(dq if dq or not sq else sq)
        key = attrs.get("name") or attrs.get("property")
        if key and "content" in attrs and key not in meta:
            meta[key] = attrs["content"]
    return meta


_album_cache: dict = {}
_album_cache_lock = threading.Lock()


def _album_info(album_id: str, timeout: int = 15) -> dict:
    with _album_cache_lock:
        if album_id in _album_cache:
            return _album_cache[album_id]
    try:
        entity = _get_entity("album", album_id, timeout=timeout)
        rd = entity.get("releaseDate")
        info = {
            "name": entity.get("name") or entity.get("title") or "",
            "cover": _extract_cover(entity),
            "artist": _artist_text(entity),
            "total": len(entity.get("trackList") or []),
            "date": str((rd.get("isoString") if isinstance(rd, dict) else rd) or "")[:10],
        }
    except Exception as e:  # noqa: BLE001 - enrichment is best-effort
        log.info("album %s lookup failed: %s", album_id, e)
        info = {}
    with _album_cache_lock:
        _album_cache[album_id] = info
    return info


def _enrich_one(track: Track, timeout: int = 15) -> None:
    if not track.spotify_id:
        return
    try:
        resp = _http().get(f"https://open.spotify.com/track/{track.spotify_id}", timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException as e:
        log.info("track page %s failed: %s", track.spotify_id, e)
        return

    meta = _parse_meta(resp.text)
    album_url = meta.get("music:album", "")
    album_id = album_url.split("?")[0].rstrip("/").rsplit("/", 1)[-1]
    if album_id and _ID_RE.match(album_id):
        info = _album_info(album_id)
        if info.get("name"):
            track.album = info["name"]
        if info.get("cover"):
            track.cover_url = info["cover"]
        track.album_artist = info.get("artist") or track.album_artist
        track.total_tracks = info.get("total") or track.total_tracks
        track.release_date = info.get("date") or track.release_date
    else:
        log.info("track page %s had no album link", track.spotify_id)

    num = meta.get("music:album:track", "")
    if num.isdigit():
        track.track_number = int(num)
    date = meta.get("music:release_date", "")
    if date and not track.release_date:
        track.release_date = date[:10]


def _enrich_track_albums(
    tracks: list[Track], workers: int = 6, progress: Optional[Callable[[int, int], None]] = None
) -> None:
    """Fill in each playlist track's real album (the playlist page itself
    only gives title/artist). Fetches each track's public page, which is
    the same no-login data Spotify shows a logged-out visitor."""
    total = len(tracks)
    done = 0
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="enrich") as pool:
        for _ in pool.map(_enrich_one, tracks):
            done += 1
            if progress:
                progress(done, total)


def fetch_tracks(url: str) -> list[Track]:
    return fetch_collection(url).tracks
