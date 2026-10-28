"""YouTube / YouTube Music as a track source: playlists, albums, single
videos, and free-text song search. Tracks come back with their video ID
already known, so the downloader skips matching entirely.

Uses ytmusicapi's unauthenticated client -- public content only.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

from .track import Collection, Track
from .youtube_match import best_thumbnail, client

_HOSTS = ("youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be")
_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
# Bits of video titles that aren't part of the song name.
_TITLE_NOISE_RE = re.compile(
    r"\s*[\(\[][^\)\]]*\b(official|video|audio|lyrics?|visuali[sz]er|hd|hq|4k|mv|m/v)\b[^\)\]]*[\)\]]",
    re.I,
)


def is_youtube_link(text: str) -> bool:
    text = text.strip()
    return urlparse(text if "://" in text else "https://" + text).netloc.lower() in _HOSTS


def parse_youtube_url(url: str) -> tuple[str, str]:
    """('video'|'playlist'|'album', id)."""
    text = url.strip()
    parsed = urlparse(text if "://" in text else "https://" + text)
    host = parsed.netloc.lower()
    if host not in _HOSTS:
        raise ValueError(f"Not a YouTube link: {url}")
    query = parse_qs(parsed.query)
    path = [p for p in parsed.path.split("/") if p]

    if host == "youtu.be" and path and _VIDEO_ID_RE.match(path[0]):
        return "video", path[0]
    if path and path[0] in ("shorts", "live", "embed") and len(path) > 1 and _VIDEO_ID_RE.match(path[1]):
        return "video", path[1]
    if path and path[0] == "browse" and len(path) > 1:
        browse_id = path[1]
        if browse_id.startswith("MPRE"):
            return "album", browse_id
        if browse_id.startswith("VL"):
            return "playlist", browse_id[2:]

    list_id = (query.get("list") or [""])[0]
    video_id = (query.get("v") or [""])[0]
    # "RD..." lists are endless auto-generated radio mixes, not playlists.
    if list_id and not list_id.startswith("RD") and (path[:1] == ["playlist"] or not video_id):
        return "playlist", list_id
    if video_id and _VIDEO_ID_RE.match(video_id):
        return "video", video_id
    if list_id and not list_id.startswith("RD"):
        return "playlist", list_id
    raise ValueError(f"Couldn't find a video, playlist or album in: {url}")


def _artists(item: dict) -> str:
    return ", ".join(a.get("name", "") for a in item.get("artists") or [] if isinstance(a, dict) and a.get("name"))


def _video_type(item: dict) -> str:
    # ATV = the official audio track; OMV/UGC/... are videos.
    if item.get("resultType") == "song" or item.get("videoType") == "MUSIC_VIDEO_TYPE_ATV":
        return "song"
    return "video"


def clean_video_title(title: str, author: str) -> tuple[str, str]:
    """(title, artist) for a plain YouTube upload: drops "(Official Video)"
    noise, " - Topic"/"VEVO" channel suffixes, and splits "Artist - Song"."""
    artist = re.sub(r"\s*-\s*Topic$|VEVO$", "", author or "").strip()
    title = _TITLE_NOISE_RE.sub("", title or "").strip()
    left, sep, right = title.partition(" - ")
    if sep and left.strip() and right.strip():
        # The uploader's "Artist - Song" beats the channel name ("XYZ Records").
        return right.strip(), left.strip()
    return title, artist


def _track_from_item(item: dict, position: int, playlist: str, album: str = "", **extra) -> Track:
    album_info = item.get("album")
    album_name = album_info.get("name", "") if isinstance(album_info, dict) else (album_info or album)
    seconds = item.get("duration_seconds") or 0
    return Track(
        title=(item.get("title") or "").strip(),
        artist=_artists(item),
        album=album_name or "",
        position=position,
        duration_ms=int(seconds) * 1000,
        cover_url=best_thumbnail(item.get("thumbnails")),
        explicit=bool(item.get("isExplicit")),
        playlist=playlist,
        video_id=item.get("videoId") or "",
        video_type=_video_type(item),
        **extra,
    )


def _fetch_album(browse_id: str) -> Collection:
    data = client().get_album(browse_id)
    name = data.get("title") or ""
    cover = best_thumbnail(data.get("thumbnails"))
    album_artist = _artists(data)
    year = str(data.get("year") or "")
    items = [t for t in data.get("tracks") or [] if t.get("videoId")]
    tracks = []
    for i, item in enumerate(items, start=1):
        t = _track_from_item(
            item, i, name, album=name,
            album_artist=album_artist,
            track_number=item.get("trackNumber") or i,
            total_tracks=len(items),
            release_date=year,
        )
        t.album = name  # album tracks report the album as a bare string or not at all
        t.cover_url = cover or t.cover_url
        t.artist = t.artist or album_artist
        t.video_type = "song"
        tracks.append(t)
    return Collection(kind="youtube-album", name=name, cover_url=cover, tracks=tracks)


def _fetch_playlist(playlist_id: str) -> Collection:
    yt = client()
    # "OLAK5uy_..." playlists are albums in disguise; the album view has
    # proper track numbers, year and album artist.
    if playlist_id.startswith("OLAK5uy_"):
        browse_id = yt.get_album_browse_id(playlist_id)
        if browse_id:
            return _fetch_album(browse_id)

    data = yt.get_playlist(playlist_id, limit=None)
    name = data.get("title") or "YouTube playlist"
    tracks = [
        _track_from_item(item, i, name)
        for i, item in enumerate(
            (t for t in data.get("tracks") or [] if t.get("videoId") and t.get("isAvailable", True)), start=1
        )
    ]
    return Collection(kind="youtube-playlist", name=name, cover_url=best_thumbnail(data.get("thumbnails")),
                      tracks=tracks)


def _fetch_video(video_id: str) -> Collection:
    details = client().get_song(video_id).get("videoDetails") or {}
    if not details.get("title"):
        raise ValueError("YouTube returned no details for that video -- is it public?")
    is_song = details.get("musicVideoType") == "MUSIC_VIDEO_TYPE_ATV"
    if is_song:
        title, artist = details["title"], re.sub(r"\s*-\s*Topic$", "", details.get("author") or "")
    else:
        title, artist = clean_video_title(details["title"], details.get("author") or "")
    track = Track(
        title=title,
        artist=artist,
        position=1,
        duration_ms=int(details.get("lengthSeconds") or 0) * 1000,
        cover_url=best_thumbnail((details.get("thumbnail") or {}).get("thumbnails")),
        video_id=video_id,
        video_type="song" if is_song else "video",
    )
    track.playlist = track.display_name
    return Collection(kind="youtube-video", name=track.display_name, cover_url=track.cover_url, tracks=[track])


def fetch_collection(url: str) -> Collection:
    kind, item_id = parse_youtube_url(url)
    if kind == "album":
        coll = _fetch_album(item_id)
    elif kind == "playlist":
        coll = _fetch_playlist(item_id)
    else:
        coll = _fetch_video(item_id)
    if not coll.tracks:
        raise ValueError("No playable tracks found -- the playlist may be empty or private.")
    return coll


def search(query: str) -> Collection:
    """Free-text search ("artist - song") -> the best song result."""
    yt = client()
    results = [r for r in yt.search(query, filter="songs", limit=5) if r.get("videoId")]
    if not results:
        results = [r for r in yt.search(query, filter="videos", limit=5) if r.get("videoId")]
    if not results:
        raise ValueError(f"Nothing found on YouTube Music for “{query}”.")
    track = _track_from_item(results[0], 1, query)
    if track.video_type == "video":
        track.title, track.artist = clean_video_title(track.title, track.artist)
    return Collection(kind="search", name=track.display_name, cover_url=track.cover_url, tracks=[track])
