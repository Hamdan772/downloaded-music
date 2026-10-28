"""Fallback metadata source: read tracks from a CSV file (e.g. a TuneMyMusic
or Exportify export of a Spotify playlist).

Accepts flexible column names so exports from different tools work:
  title / track / track name / name
  artist / artist name / artist name(s) / artists
  album / album name
plus optional duration, track number, album artist, release date and ISRC
columns (Exportify includes all of these).
"""
from __future__ import annotations

import csv
import re

from .track import Track

_TITLE_KEYS = ("title", "track", "track name", "song", "song name", "name")
_ARTIST_KEYS = ("artist", "artist name", "artist name(s)", "artist names", "artists")
_ALBUM_KEYS = ("album", "album name")
_ALBUM_ARTIST_KEYS = ("album artist", "album artist name(s)", "album artist name", "album artists")
_DURATION_MS_KEYS = ("duration (ms)", "duration_ms", "duration ms", "track duration (ms)")
_DURATION_KEYS = ("duration", "length", "time")
_TRACK_NUMBER_KEYS = ("track number", "track_number", "track no", "tracknumber")
_DATE_KEYS = ("release date", "album release date", "year", "date")
_ISRC_KEYS = ("isrc",)
_ID_KEYS = ("spotify id", "track uri", "spotify uri", "uri")


def _find(row: dict, keys) -> str:
    lower = {k.strip().lower(): v for k, v in row.items() if k}
    for key in keys:
        value = lower.get(key)
        if value and value.strip():
            return value.strip()
    return ""


def _parse_duration_ms(row: dict) -> int:
    ms = _find(row, _DURATION_MS_KEYS)
    if ms.replace(".", "", 1).isdigit():
        return int(float(ms))
    text = _find(row, _DURATION_KEYS)
    # "3:25" / "1:02:03", or plain seconds
    if re.fullmatch(r"\d+(:\d{1,2}){1,2}", text):
        seconds = 0
        for part in text.split(":"):
            seconds = seconds * 60 + int(part)
        return seconds * 1000
    if text.isdigit():
        n = int(text)
        return n if n > 10_000 else n * 1000  # already ms vs seconds
    return 0


def fetch_tracks(csv_path: str) -> list[Track]:
    tracks = []
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        header = f.readline()
        f.seek(0)
        dialect = csv.excel
        if "," not in header:
            # European-locale spreadsheet exports use ';' (or tabs).
            try:
                dialect = csv.Sniffer().sniff(header, delimiters=";\t")
            except csv.Error:
                pass
        reader = csv.DictReader(f, dialect=dialect)
        for row in reader:
            title = _find(row, _TITLE_KEYS)
            if not title:
                continue
            number = _find(row, _TRACK_NUMBER_KEYS)
            spotify_id = _find(row, _ID_KEYS).rsplit(":", 1)[-1]
            tracks.append(
                Track(
                    title=title,
                    # Exportify separates multiple artists with commas already;
                    # some tools use semicolons.
                    artist=_find(row, _ARTIST_KEYS).replace(";", ","),
                    album=_find(row, _ALBUM_KEYS),
                    album_artist=_find(row, _ALBUM_ARTIST_KEYS).replace(";", ","),
                    track_number=int(number) if number.isdigit() else 0,
                    release_date=_find(row, _DATE_KEYS)[:10],
                    position=len(tracks) + 1,
                    duration_ms=_parse_duration_ms(row),
                    isrc=_find(row, _ISRC_KEYS),
                    spotify_id=spotify_id,
                )
            )
    if not tracks:
        raise ValueError(
            f"No usable rows found in {csv_path}. Expected columns like "
            "'Title'/'Track Name' and 'Artist'."
        )
    return tracks
