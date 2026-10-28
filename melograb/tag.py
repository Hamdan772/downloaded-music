from __future__ import annotations

import os
import re

import requests

from .track import Track


def _fetch_cover_bytes(url: str) -> bytes:
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    return resp.content


def tag_file(path: str, track: Track) -> None:
    if path.endswith(".mp3"):
        _tag_mp3(path, track)
    elif path.endswith(".m4a"):
        _tag_m4a(path, track)
    elif path.endswith(".opus"):
        _tag_opus(path, track)

    if track.lyrics_lrc:
        _write_lrc_sidecar(path, track.lyrics_lrc)
        if path.endswith(".mp3"):
            _embed_lyrics_mp3(path, track.lyrics_lrc)
        elif path.endswith(".m4a"):
            _embed_lyrics_m4a(path, track.lyrics_lrc)
        elif path.endswith(".opus"):
            _embed_lyrics_opus(path, track.lyrics_lrc)


_LRC_LINE_RE = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\](.*)")


def _parse_lrc(lrc_text: str) -> list[tuple[str, int]]:
    """[(line_text, timestamp_ms), ...] sorted by time, for ID3 SYLT."""
    lines = []
    for raw_line in lrc_text.splitlines():
        m = _LRC_LINE_RE.match(raw_line.strip())
        if not m:
            continue
        minutes, seconds, text = m.groups()
        ms = int((int(minutes) * 60 + float(seconds)) * 1000)
        lines.append((text.strip() or " ", ms))
    lines.sort(key=lambda p: p[1])
    return lines


def _write_lrc_sidecar(audio_path: str, lrc_text: str) -> None:
    stem, _ = os.path.splitext(audio_path)
    try:
        with open(f"{stem}.lrc", "w", encoding="utf-8") as f:
            f.write(lrc_text)
    except OSError:
        pass


def _embed_lyrics_mp3(path: str, lrc_text: str) -> None:
    from mutagen.id3 import ID3, SYLT, USLT

    tags = ID3(path)
    # USLT carries the raw LRC text -- the convention most players/taggers
    # rely on for "synced-ish" lyrics display even without SYLT support.
    tags["USLT::eng"] = USLT(encoding=3, lang="eng", desc="", text=lrc_text)

    synced = _parse_lrc(lrc_text)
    if synced:
        tags["SYLT::eng"] = SYLT(
            encoding=3, lang="eng", format=2, type=1, desc="", text=synced
        )
    tags.save(path, v2_version=3)


def _embed_lyrics_m4a(path: str, lrc_text: str) -> None:
    from mutagen.mp4 import MP4

    audio = MP4(path)
    audio["\xa9lyr"] = [lrc_text]
    audio.save()


def _embed_lyrics_opus(path: str, lrc_text: str) -> None:
    from mutagen.oggopus import OggOpus

    audio = OggOpus(path)
    audio["LYRICS"] = lrc_text
    audio.save()


def _tag_mp3(path: str, track: Track) -> None:
    from mutagen.id3 import APIC, ID3, ID3NoHeaderError, TALB, TIT2, TPE1, TRCK

    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()

    tags["TIT2"] = TIT2(encoding=3, text=track.title)
    tags["TPE1"] = TPE1(encoding=3, text=track.artist)
    if track.album:
        tags["TALB"] = TALB(encoding=3, text=track.album)
    if track.track_number:
        tags["TRCK"] = TRCK(encoding=3, text=str(track.track_number))

    if track.cover_url:
        try:
            cover = _fetch_cover_bytes(track.cover_url)
            tags["APIC"] = APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=cover)
        except Exception:
            pass

    tags.save(path, v2_version=3)


def _tag_m4a(path: str, track: Track) -> None:
    from mutagen.mp4 import MP4, MP4Cover

    audio = MP4(path)
    audio["\xa9nam"] = [track.title]
    audio["\xa9ART"] = [track.artist]
    if track.album:
        audio["\xa9alb"] = [track.album]
    if track.track_number:
        audio["trkn"] = [(track.track_number, 0)]

    if track.cover_url:
        try:
            cover = _fetch_cover_bytes(track.cover_url)
            audio["covr"] = [MP4Cover(cover, imageformat=MP4Cover.FORMAT_JPEG)]
        except Exception:
            pass

    audio.save()


def _tag_opus(path: str, track: Track) -> None:
    from mutagen.oggopus import OggOpus

    audio = OggOpus(path)
    audio["title"] = track.title
    audio["artist"] = track.artist
    if track.album:
        audio["album"] = track.album
    if track.track_number:
        audio["tracknumber"] = str(track.track_number)
    audio.save()
