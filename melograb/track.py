from __future__ import annotations

import re
from dataclasses import dataclass, field

_ILLEGAL_CHARS_RE = re.compile(r'[<>:"|?*\x00-\x1f]')
_SLASHES_RE = re.compile(r"[\\/]+")
_SPACES_RE = re.compile(r"\s+")
_MAX_NAME_BYTES = 180  # leaves room for extension + temp suffixes under the 255-byte limit


def sanitize_filename(name: str, fallback: str = "Unknown") -> str:
    """Make `name` safe as a single path component on macOS, Linux and Windows."""
    name = _SLASHES_RE.sub("-", name)
    name = _ILLEGAL_CHARS_RE.sub("", name)
    name = _SPACES_RE.sub(" ", name).strip().strip(".").strip()
    encoded = name.encode("utf-8")
    if len(encoded) > _MAX_NAME_BYTES:
        name = encoded[:_MAX_NAME_BYTES].decode("utf-8", "ignore").rstrip(" .")
    return name or fallback


@dataclass
class Track:
    title: str
    artist: str
    album: str = ""
    album_artist: str = ""
    track_number: int = 0
    total_tracks: int = 0
    disc_number: int = 0
    total_discs: int = 0
    genre: str = ""
    release_date: str = ""
    # 1-based position in the source playlist/CSV -- used for ordering
    # (e.g. the .m3u8 playlist), never written as the track number.
    position: int = 0
    duration_ms: int = 0
    cover_url: str = ""
    # True when cover_url is the release's own album artwork (Spotify album,
    # YouTube Music album, Apple Music) rather than a playlist cover or a
    # video thumbnail -- only then is it kept over a looked-up official cover.
    cover_is_official: bool = False
    isrc: str = ""
    spotify_id: str = ""
    explicit: bool = False
    # Name of the playlist/album/search this track came from (for templates).
    playlist: str = ""
    # Known YouTube video for this track (YouTube Music sources, or a link
    # the user picked by hand) -- skips matching. video_type is "song" for
    # official audio, "video" for music videos/uploads.
    video_id: str = ""
    video_type: str = ""
    # Re-download even if the output file exists (e.g. after fixing a match).
    replace_existing: bool = False
    lyrics_lrc: str = ""
    lyrics_plain: str = ""

    @property
    def search_query(self) -> str:
        return f"{self.artist} - {self.title}" if self.artist else self.title

    @property
    def filename_stem(self) -> str:
        if self.artist:
            return sanitize_filename(f"{self.artist} - {self.title}")
        return sanitize_filename(self.title)

    @property
    def album_folder_name(self) -> str:
        return sanitize_filename(self.album, fallback="Unknown Album")

    @property
    def display_name(self) -> str:
        return f"{self.artist} - {self.title}" if self.artist else self.title

    @property
    def youtube_url(self) -> str:
        return f"https://music.youtube.com/watch?v={self.video_id}" if self.video_id else ""

    @property
    def duration_text(self) -> str:
        if not self.duration_ms:
            return ""
        seconds = round(self.duration_ms / 1000)
        return f"{seconds // 60}:{seconds % 60:02d}"


@dataclass
class Collection:
    """A batch of tracks from one source (playlist, album, search, CSV...)."""

    kind: str  # "playlist", "album", "track", "artist", "search", "csv", "youtube-*", "multiple"
    name: str
    cover_url: str = ""
    tracks: list = field(default_factory=list)
