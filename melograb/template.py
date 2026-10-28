"""Output path templates, e.g. "{album_artist}/{album}/{track:02} {title}".

`/` separates folders. Every folder/file component is sanitized on its own,
so a "/" inside a song title can never create a stray subfolder.
"""
from __future__ import annotations

import os
import string

from .track import Track, sanitize_filename

DEFAULT_TEMPLATE = "{album}/{artist} - {title}"
FLAT_TEMPLATE = "{artist} - {title}"

# Shown in the GUI's Layout picker, in this order.
PRESETS = {
    "Album folders": DEFAULT_TEMPLATE,
    "Flat": FLAT_TEMPLATE,
    "Artist / Album / ## Title": "{album_artist}/{album}/{track:02} {title}",
    "Playlist folder, in order": "{playlist}/{position:03} {artist} - {title}",
}

FIELDS = (
    "title", "artist", "album", "album_artist", "track", "total_tracks",
    "year", "date", "playlist", "position",
)


def _values(track: Track) -> dict:
    artist = track.artist or "Unknown Artist"
    return {
        "title": track.title or "Unknown Title",
        "artist": artist,
        "album": track.album or "Unknown Album",
        # Players group albums by album artist; fall back to the main artist
        # rather than "A, B, C" so features don't split an album.
        "album_artist": track.album_artist or artist.split(",")[0].strip() or "Unknown Artist",
        "track": track.track_number,
        "total_tracks": track.total_tracks,
        "year": track.release_date[:4],
        "date": track.release_date,
        "playlist": track.playlist or "Unknown Playlist",
        "position": track.position,
    }


class _Formatter(string.Formatter):
    def get_value(self, key, args, kwargs):
        if key not in kwargs:
            raise ValueError(f"unknown field {{{key}}} -- available: {', '.join('{' + f + '}' for f in FIELDS)}")
        return kwargs[key]


_formatter = _Formatter()


def validate(template: str) -> None:
    """Raise ValueError with a readable message if `template` is unusable."""
    if not template or not template.strip():
        raise ValueError("template is empty")
    if template.startswith(("/", "\\")) or os.path.isabs(template):
        raise ValueError("template must be a relative path")
    parts = template.split("/")
    if any(p.strip() in ("", ".", "..") for p in parts):
        raise ValueError("template has an empty, '.' or '..' folder")
    if "{title}" not in parts[-1] and "{title:" not in parts[-1]:
        raise ValueError("the file name part (after the last /) must include {title}")
    try:
        render(template, Track(title="t", artist="a"))
    except (ValueError, KeyError, IndexError) as e:
        raise ValueError(f"invalid template: {e}") from e


def render(template: str, track: Track) -> str:
    """Relative path (OS separators, no extension) for `track`."""
    values = _values(track)
    parts = []
    for i, part in enumerate(template.split("/")):
        text = _formatter.format(part, **values)
        is_file = i == len(template.split("/")) - 1
        parts.append(sanitize_filename(text, fallback=track.filename_stem if is_file else "Unknown"))
    return os.path.join(*parts)


def preset_name(template: str) -> str:
    for name, value in PRESETS.items():
        if value == template:
            return name
    return ""
