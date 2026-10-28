from __future__ import annotations

import base64
import functools
import io
import logging
import os
import re

import requests

from .track import Track

log = logging.getLogger(__name__)


# How lyrics go into files:
#   synced      timestamped lyrics inside the song (ID3 SYLT + the LRC text)
#   synced+lrc  the same, plus a .lrc file next to the song
#   plain       untimed text only (e.g. for Apple Music, which shows raw LRC)
#   off         no lyrics
LYRICS_MODES = ("synced", "synced+lrc", "plain", "off")


@functools.lru_cache(maxsize=64)
def _fetch_cover(url: str) -> tuple[bytes, str]:
    """(image bytes, mime) -- cached, since every track of an album shares
    one cover. Square JPEG/PNG artwork is used as-is; anything else (video
    thumbnails, WebP) becomes a square JPEG, which every player shows."""
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    return square_cover(resp.content)


def square_cover(data: bytes) -> tuple[bytes, str]:
    from PIL import Image

    img = Image.open(io.BytesIO(data))
    w, h = img.size
    is_square = abs(w - h) <= max(w, h) * 0.02
    if is_square and data.startswith(b"\xff\xd8"):
        return data, "image/jpeg"
    if is_square and data.startswith(b"\x89PNG"):
        return data, "image/png"
    img = img.convert("RGB")
    if not is_square:
        # Video thumbnails are letterboxed: trim near-black bars, then take
        # the centre square.
        mask = img.convert("L").point(lambda v: 255 if v > 24 else 0)
        box = mask.getbbox()
        if box and (box[2] - box[0]) * (box[3] - box[1]) > 0.3 * w * h:
            img = img.crop(box)
            w, h = img.size
        side = min(w, h)
        left, top = (w - side) // 2, (h - side) // 2
        img = img.crop((left, top, left + side, top + side))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=92)
    return buf.getvalue(), "image/jpeg"


def _cover(track: Track):
    if not track.cover_url:
        return None
    try:
        return _fetch_cover(track.cover_url)
    except Exception as e:  # noqa: BLE001 - cover art is best-effort
        log.info("cover fetch failed for %s: %s", track.cover_url, e)
        return None


def tag_file(path: str, track: Track, lyrics: str = "synced") -> None:
    """Write all metadata, cover art and (per `lyrics`, see LYRICS_MODES)
    lyrics into the file."""
    if lyrics not in LYRICS_MODES:
        raise ValueError(f"unknown lyrics mode {lyrics!r}")
    ext = os.path.splitext(path)[1].lower()
    if ext == ".mp3":
        _tag_mp3(path, track, lyrics)
    elif ext == ".m4a":
        _tag_m4a(path, track, lyrics)
    elif ext == ".opus":
        _tag_opus(path, track, lyrics)

    if lyrics == "synced+lrc" and track.lyrics_lrc:
        _write_lrc_sidecar(path, track.lyrics_lrc)


_LRC_LINE_RE = re.compile(r"^((?:\[\d+:\d+(?:[.:]\d+)?\])+)(.*)$")
_LRC_TIME_RE = re.compile(r"\[(\d+):(\d+(?:[.:]\d+)?)\]")
_LRC_META_RE = re.compile(r"^\[[a-z]+:.*\]$", re.I)


def strip_timestamps(lrc_text: str) -> str:
    """LRC -> plain lyrics (drops timestamps and [ar:]/[ti:] header lines)."""
    lines = []
    for raw in lrc_text.splitlines():
        line = raw.strip()
        m = _LRC_LINE_RE.match(line)
        if m:
            lines.append(m.group(2).strip())
        elif not _LRC_META_RE.match(line):
            lines.append(line)
    return "\n".join(lines).strip()


def _parse_lrc(lrc_text: str) -> list[tuple[str, int]]:
    """[(line_text, timestamp_ms), ...] sorted by time, for ID3 SYLT.
    Handles lines with several timestamps ("[00:12.00][01:30.00]chorus")."""
    lines = []
    for raw_line in lrc_text.splitlines():
        m = _LRC_LINE_RE.match(raw_line.strip())
        if not m:
            continue
        stamps, text = m.groups()
        for minutes, seconds in _LRC_TIME_RE.findall(stamps):
            ms = int((int(minutes) * 60 + float(seconds.replace(":", "."))) * 1000)
            lines.append((text.strip() or " ", ms))
    lines.sort(key=lambda p: p[1])
    return lines


def _write_lrc_sidecar(audio_path: str, lrc_text: str) -> None:
    stem, _ = os.path.splitext(audio_path)
    try:
        with open(f"{stem}.lrc", "w", encoding="utf-8") as f:
            f.write(lrc_text)
    except OSError as e:
        log.info("couldn't write .lrc for %s: %s", audio_path, e)


def _lyrics_text(track: Track, mode: str) -> str:
    if mode == "off":
        return ""
    if mode == "plain":
        return track.lyrics_plain or strip_timestamps(track.lyrics_lrc)
    # Timestamped LRC text is what most players (Poweramp, MusicBee, Plex,
    # Navidrome, ...) read for synced lyrics, even those without SYLT.
    return track.lyrics_lrc or track.lyrics_plain


def _disc_text(track: Track) -> str:
    if track.disc_number and track.total_discs:
        return f"{track.disc_number}/{track.total_discs}"
    return str(track.disc_number)


def _track_text(track: Track) -> str:
    if track.track_number and track.total_tracks:
        return f"{track.track_number}/{track.total_tracks}"
    return str(track.track_number)


def _tag_mp3(path: str, track: Track, lyrics: str = "synced") -> None:
    from mutagen.id3 import (
        APIC,
        COMM,
        ID3,
        SYLT,
        TALB,
        TCON,
        TDRC,
        TIT2,
        TPE1,
        TPE2,
        TPOS,
        TRCK,
        TSRC,
        TXXX,
        USLT,
    )

    tags = ID3()  # start clean -- don't keep whatever the encoder wrote
    tags.add(TIT2(encoding=3, text=track.title))
    if track.artist:
        tags.add(TPE1(encoding=3, text=track.artist))
    if track.album:
        tags.add(TALB(encoding=3, text=track.album))
    if track.album_artist:
        tags.add(TPE2(encoding=3, text=track.album_artist))
    if track.track_number:
        tags.add(TRCK(encoding=3, text=_track_text(track)))
    if track.disc_number:
        tags.add(TPOS(encoding=3, text=_disc_text(track)))
    if track.genre:
        tags.add(TCON(encoding=3, text=track.genre))
    if track.release_date:
        tags.add(TDRC(encoding=3, text=track.release_date))
    if track.isrc:
        tags.add(TSRC(encoding=3, text=track.isrc))
    if track.youtube_url:
        tags.add(COMM(encoding=3, lang="eng", desc="", text=f"Source: {track.youtube_url}"))
    if track.explicit:
        tags.add(TXXX(encoding=3, desc="ITUNESADVISORY", text="1"))

    cover = _cover(track)
    if cover:
        data, mime = cover
        tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=data))

    lyrics_text = _lyrics_text(track, lyrics)
    if lyrics_text:
        tags.add(USLT(encoding=3, lang="eng", desc="", text=lyrics_text))
    synced = _parse_lrc(track.lyrics_lrc) if track.lyrics_lrc and lyrics.startswith("synced") else []
    if synced:
        tags.add(SYLT(encoding=3, lang="eng", format=2, type=1, desc="", text=synced))

    tags.save(path, v2_version=3)


def _tag_m4a(path: str, track: Track, lyrics: str = "synced") -> None:
    from mutagen.mp4 import MP4, MP4Cover

    audio = MP4(path)
    if audio.tags is None:
        audio.add_tags()
    audio.tags.clear()
    audio["\xa9nam"] = [track.title]
    if track.artist:
        audio["\xa9ART"] = [track.artist]
    if track.album:
        audio["\xa9alb"] = [track.album]
    if track.album_artist:
        audio["aART"] = [track.album_artist]
    if track.track_number:
        audio["trkn"] = [(track.track_number, track.total_tracks or 0)]
    if track.disc_number:
        audio["disk"] = [(track.disc_number, track.total_discs or 0)]
    if track.genre:
        audio["\xa9gen"] = [track.genre]
    if track.release_date:
        audio["\xa9day"] = [track.release_date]
    if track.youtube_url:
        audio["\xa9cmt"] = [f"Source: {track.youtube_url}"]
    if track.explicit:
        audio["rtng"] = [1]

    cover = _cover(track)
    if cover:
        data, mime = cover
        fmt = MP4Cover.FORMAT_PNG if mime == "image/png" else MP4Cover.FORMAT_JPEG
        audio["covr"] = [MP4Cover(data, imageformat=fmt)]

    lyrics_text = _lyrics_text(track, lyrics)
    if lyrics_text:
        audio["\xa9lyr"] = [lyrics_text]

    audio.save()


def _tag_opus(path: str, track: Track, lyrics: str = "synced") -> None:
    from mutagen.flac import Picture
    from mutagen.oggopus import OggOpus

    audio = OggOpus(path)
    audio.tags.clear()
    audio["title"] = track.title
    if track.artist:
        audio["artist"] = track.artist
    if track.album:
        audio["album"] = track.album
    if track.album_artist:
        audio["albumartist"] = track.album_artist
    if track.track_number:
        audio["tracknumber"] = str(track.track_number)
    if track.total_tracks:
        audio["tracktotal"] = str(track.total_tracks)
    if track.disc_number:
        audio["discnumber"] = str(track.disc_number)
    if track.total_discs:
        audio["disctotal"] = str(track.total_discs)
    if track.genre:
        audio["genre"] = track.genre
    if track.release_date:
        audio["date"] = track.release_date
    if track.isrc:
        audio["isrc"] = track.isrc
    if track.youtube_url:
        audio["comment"] = f"Source: {track.youtube_url}"
    if track.explicit:
        audio["itunesadvisory"] = "1"

    cover = _cover(track)
    if cover:
        data, mime = cover
        pic = Picture()
        pic.type = 3
        pic.mime = mime
        pic.desc = "Cover"
        pic.data = data
        audio["metadata_block_picture"] = base64.b64encode(pic.write()).decode("ascii")

    lyrics_text = _lyrics_text(track, lyrics)
    if lyrics_text:
        audio["lyrics"] = lyrics_text

    audio.save()


# ---------- lyrics for files that already exist (keeps every other tag) ----------


def has_lyrics(path: str) -> bool:
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".mp3":
            from mutagen.id3 import ID3, ID3NoHeaderError

            try:
                tags = ID3(path)
            except ID3NoHeaderError:
                return False
            return bool(tags.getall("USLT") or tags.getall("SYLT"))
        if ext == ".m4a":
            from mutagen.mp4 import MP4

            return bool((MP4(path).tags or {}).get("\xa9lyr"))
        from mutagen import File

        audio = File(path)
        return bool(audio and audio.tags and (audio.tags.get("lyrics") or audio.tags.get("unsyncedlyrics")))
    except Exception:  # noqa: BLE001 - unreadable file: treat as "no lyrics"
        return False


def embed_lyrics(path: str, synced: str, plain: str) -> None:
    """Add lyrics to an existing file without touching its other tags, and
    write a .lrc sidecar when they're synced."""
    text = synced or plain
    if not text:
        return
    ext = os.path.splitext(path)[1].lower()
    if ext == ".mp3":
        from mutagen.id3 import ID3, SYLT, USLT, ID3NoHeaderError

        try:
            tags = ID3(path)
        except ID3NoHeaderError:
            tags = ID3()
        tags.delall("USLT")
        tags.delall("SYLT")
        tags.add(USLT(encoding=3, lang="eng", desc="", text=text))
        parsed = _parse_lrc(synced) if synced else []
        if parsed:
            tags.add(SYLT(encoding=3, lang="eng", format=2, type=1, desc="", text=parsed))
        tags.save(path, v2_version=tags.version[1] if tags.version[1] in (3, 4) else 3)
    elif ext == ".m4a":
        from mutagen.mp4 import MP4

        audio = MP4(path)
        if audio.tags is None:
            audio.add_tags()
        audio["\xa9lyr"] = [text]
        audio.save()
    else:
        from mutagen import File

        audio = File(path)
        if audio is None:
            raise ValueError(f"unsupported file: {path}")
        if audio.tags is None:
            audio.add_tags()
        audio.tags["lyrics"] = text
        audio.save()
    if synced:
        _write_lrc_sidecar(path, synced)
