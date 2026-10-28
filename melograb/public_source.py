"""Fetch Spotify playlist/album track lists from the PUBLIC embed page --
the same page Spotify serves to anyone (logged out, no API key) to render
a preview player. No login, no developer app, no Premium required.

This only reads metadata that Spotify already publishes to anonymous
visitors; it never touches audio streams or anything behind a login wall.
"""
from __future__ import annotations

import json
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .track import Collection, Track

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


KINDS = ("playlist", "album", "track", "artist")
_SHORT_LINK_HOSTS = ("spotify.link", "spotify.app.link")
_LINK_IN_PAGE_RE = re.compile(r"open\.spotify\.com/(playlist|album|track|artist)/([A-Za-z0-9]{22})")


def is_spotify_link(text: str) -> bool:
    text = text.strip().lower()
    if text.startswith("spotify:"):
        return True
    host = urlparse(text if "://" in text else "https://" + text).netloc
    return host in ("open.spotify.com", "play.spotify.com", *_SHORT_LINK_HOSTS)


def resolve_short_link(url: str, timeout: int = 15) -> str:
    """spotify.link/... share links (from the mobile app) -> open.spotify.com URL.
    They redirect through an app-link page, so look at both where we ended
    up and the page body."""
    try:
        resp = _http().get(url if "://" in url else "https://" + url, timeout=timeout)
    except requests.RequestException as e:
        raise ValueError(f"Couldn't open the share link: {e}") from e
    for text in (resp.url, resp.text):
        m = _LINK_IN_PAGE_RE.search(text or "")
        if m:
            return f"https://open.spotify.com/{m.group(1)}/{m.group(2)}"
    raise ValueError(f"Couldn't find a Spotify playlist/album/track behind {url}")


def parse_spotify_url(url: str) -> tuple[str, str]:
    text = url.strip()
    if text.startswith("spotify:"):
        parts = text.split(":")
        if len(parts) == 3 and parts[1] in KINDS and _ID_RE.match(parts[2]):
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
    if len(parts) < 2 or parts[0].lower() not in KINDS:
        raise ValueError(f"Only playlist, album, track and artist links are supported: {url}")
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
    """Fetch a public playlist/album/track/artist. Playlist and artist tracks
    are enriched with their real album, track number and cover.
    `progress(done, total)` is called during enrichment."""
    if urlparse(url.strip() if "://" in url else "https://" + url.strip()).netloc.lower() in _SHORT_LINK_HOSTS:
        url = resolve_short_link(url)
    kind, item_id = parse_spotify_url(url)
    entity = _get_entity(kind, item_id)
    if kind == "track":
        return _single_track(entity, item_id)

    items = entity.get("trackList") or []
    name = entity.get("name") or entity.get("title") or ""
    cover = _extract_cover(entity)
    is_album = kind == "album"
    album_artist = _artist_text(entity) if is_album else ""
    release_date = _release_date(entity) if is_album else ""
    if kind == "artist":
        name = f"{name} - Top Tracks" if name else "Top Tracks"

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
                album=name if is_album else "",
                album_artist=album_artist,
                track_number=i if is_album else 0,
                total_tracks=len(items) if is_album else 0,
                release_date=release_date,
                position=i,
                duration_ms=item.get("duration") or 0,
                # A playlist's own cover is only a last-resort fallback for its
                # tracks; enrichment fills in each track's real album cover.
                cover_url=cover if is_album else "",
                cover_is_official=is_album and bool(cover),
                spotify_id=uri.rsplit(":", 1)[-1] if uri else "",
                explicit=bool(item.get("isExplicit")),
                playlist=name,
            )
        )
    if not tracks:
        raise ValueError("No tracks found -- the playlist may be empty or private.")

    if not is_album:
        _enrich_track_albums(tracks, progress=progress)

    return Collection(kind=kind, name=name, cover_url=cover, tracks=tracks)


def _release_date(entity: dict) -> str:
    rd = entity.get("releaseDate")
    value = (rd.get("isoString") if isinstance(rd, dict) else rd) or ""
    return str(value)[:10]


def _single_track(entity: dict, track_id: str) -> Collection:
    title = (entity.get("name") or entity.get("title") or "").strip()
    if not title:
        raise ValueError("Spotify's embed page returned no track data.")
    track = Track(
        title=title,
        artist=_artist_text(entity),
        release_date=_release_date(entity),
        position=1,
        duration_ms=entity.get("duration") or 0,
        cover_url=_extract_cover(entity),
        cover_is_official=True,  # a track's embed shows its album's artwork
        spotify_id=track_id,
        explicit=bool(entity.get("isExplicit")),
    )
    track.playlist = track.display_name
    return Collection(kind="track", name=track.display_name, cover_url=track.cover_url, tracks=[track])


def fetch_playlist(url: str) -> tuple[str, list[Track]]:
    """Returns (playlist/album name, tracks)."""
    collection = fetch_collection(url)
    return collection.name, collection.tracks


def _enrich_one(track: Track, timeout: int = 15) -> None:
    """Add what a playlist entry lacks from the song's own public embed: its
    album's cover art (640px), the release date and the full artist list.

    Spotify's song pages used to carry the album name in <meta> tags, but
    they're now a script-only web player; the album name comes from Apple
    Music / YouTube Music later in the pipeline instead.
    """
    if not track.spotify_id:
        return
    try:
        entity = _get_entity("track", track.spotify_id, timeout=timeout)
    except Exception as e:  # noqa: BLE001 - enrichment is best-effort
        log.info("track embed %s failed: %s", track.spotify_id, e)
        return
    cover = _extract_cover(entity)
    if cover:
        track.cover_url = cover
        track.cover_is_official = True
    track.release_date = track.release_date or _release_date(entity)
    track.artist = track.artist or _artist_text(entity)


def _enrich_track_albums(
    tracks: list[Track], workers: int = 6, progress: Optional[Callable[[int, int], None]] = None
) -> None:
    """Fill in each playlist track's album cover and release date (the
    playlist itself only lists title/artist) from its public embed -- the
    same no-login data Spotify shows a logged-out visitor."""
    total = len(tracks)
    done = 0
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="enrich") as pool:
        for _ in pool.map(_enrich_one, tracks):
            done += 1
            if progress:
                progress(done, total)


def fetch_tracks(url: str) -> list[Track]:
    return fetch_collection(url).tracks
