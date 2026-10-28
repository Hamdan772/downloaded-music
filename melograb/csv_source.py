"""Fallback metadata source: read tracks from a CSV file (e.g. a TuneMyMusic
export of a Spotify playlist) when you don't want to set up API credentials.

Accepts flexible column names so exports from different tools work:
  title / track / track name / name
  artist / artist name / artists
  album / album name
"""
from __future__ import annotations

import csv

from .track import Track

_TITLE_KEYS = ("title", "track", "track name", "song", "name")
_ARTIST_KEYS = ("artist", "artist name", "artists")
_ALBUM_KEYS = ("album", "album name")


def _find(row: dict, keys) -> str:
    lower = {k.strip().lower(): v for k, v in row.items() if k}
    for key in keys:
        if key in lower and lower[key]:
            return lower[key].strip()
    return ""


def fetch_tracks(csv_path: str) -> list[Track]:
    tracks = []
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader, start=1):
            title = _find(row, _TITLE_KEYS)
            artist = _find(row, _ARTIST_KEYS)
            if not title:
                continue
            tracks.append(
                Track(
                    title=title,
                    artist=artist,
                    album=_find(row, _ALBUM_KEYS),
                    track_number=i,
                )
            )
    if not tracks:
        raise ValueError(
            f"No usable rows found in {csv_path}. Expected columns like "
            "'Title'/'Track Name' and 'Artist'."
        )
    return tracks
