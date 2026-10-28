"""One entry point for every kind of input: Spotify links, YouTube/YouTube
Music links, CSV files, and free-text song searches -- several at once."""
from __future__ import annotations

import logging
import os
import re
from typing import Callable, Optional

from . import csv_source, public_source, ytmusic_source
from .track import Collection

log = logging.getLogger(__name__)

_URLISH_RE = re.compile(r"^(https?://|spotify:|[\w.-]+\.(com|be|link)/)", re.I)


def split_inputs(text: str) -> list[str]:
    """Each line is either links (split on whitespace) or one search query,
    so 'link1 link2' works and so does 'mac demarco - chamber of reflection'."""
    items = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        tokens = line.split()
        if any(_URLISH_RE.match(tok) for tok in tokens):
            items.extend(tok for tok in tokens if _URLISH_RE.match(tok) or os.path.isfile(tok))
        else:
            items.append(line)
    return items


def describe(item: str) -> str:
    """'spotify' | 'youtube' | 'csv' | 'search' -- what resolve() will do."""
    if public_source.is_spotify_link(item):
        return "spotify"
    if ytmusic_source.is_youtube_link(item):
        return "youtube"
    if item.lower().endswith(".csv") and os.path.isfile(os.path.expanduser(item)):
        return "csv"
    if _URLISH_RE.match(item):
        raise ValueError(f"Unsupported link: {item} -- use a Spotify or YouTube (Music) link.")
    return "search"


def resolve(item: str, progress: Optional[Callable[[int, int], None]] = None) -> Collection:
    kind = describe(item)
    if kind == "spotify":
        return public_source.fetch_collection(item, progress=progress)
    if kind == "youtube":
        return ytmusic_source.fetch_collection(item)
    if kind == "csv":
        path = os.path.expanduser(item)
        name = os.path.splitext(os.path.basename(path))[0]
        tracks = csv_source.fetch_tracks(path)
        for t in tracks:
            t.playlist = name
        return Collection(kind="csv", name=name, tracks=tracks)
    return ytmusic_source.search(item)


def resolve_many(
    items: list[str],
    progress: Optional[Callable[[int, int], None]] = None,
    on_source: Optional[Callable[[int, int, str], None]] = None,
) -> Collection:
    """Resolve and concatenate several inputs. `on_source(i, n, item)` is
    called before each one."""
    if not items:
        raise ValueError("Nothing to fetch -- paste a link or type a song.")
    if len(items) == 1:
        return resolve(items[0], progress=progress)

    collections = []
    for i, item in enumerate(items):
        if on_source:
            on_source(i, len(items), item)
        collections.append(resolve(item, progress=progress))

    tracks = [t for c in collections for t in c.tracks]
    for pos, t in enumerate(tracks, start=1):
        t.position = pos
    if all(c.kind in ("search", "track", "youtube-video") for c in collections):
        name = "Songs"
    else:
        name = " + ".join(c.name for c in collections if c.name)[:120] or "Mix"
    return Collection(kind="multiple", name=name, cover_url=collections[0].cover_url, tracks=tracks)
