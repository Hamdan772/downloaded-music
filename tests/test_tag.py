import os

import pytest
from conftest import make_audio, requires_ffmpeg

from melograb import tag

_real_fetch_cover = tag._fetch_cover  # captured before the autouse fake replaces it

LRC = "[00:01.00]first line\n[00:02.50][00:04.00]chorus\nnot a lyric line\n[00:03.00]"


@pytest.fixture(autouse=True)
def fake_cover(monkeypatch, jpeg_bytes):
    monkeypatch.setattr(tag, "_fetch_cover", lambda url: (jpeg_bytes, "image/jpeg"))


def test_parse_lrc_handles_multiple_timestamps():
    assert tag._parse_lrc(LRC) == [
        ("first line", 1000),
        ("chorus", 2500),
        (" ", 3000),
        ("chorus", 4000),
    ]


@requires_ffmpeg
def test_mp3_tags(tmp_path, track, jpeg_bytes):
    from mutagen.id3 import ID3

    path = make_audio(str(tmp_path / "a.mp3"))
    track.lyrics_lrc = LRC
    tag.tag_file(path, track)
    tags = ID3(path)
    assert str(tags["TIT2"]) == "Mosquito"
    assert str(tags["TPE1"]) == "PinkPantheress"
    assert str(tags["TALB"]) == "Fancy That"
    assert str(tags["TPE2"]) == "PinkPantheress"
    assert str(tags["TRCK"]) == "3/9"
    assert tags.getall("APIC")[0].data == jpeg_bytes
    assert tags.getall("USLT")[0].text == LRC
    assert tags.getall("SYLT")[0].text[0] == ("first line", 1000)
    assert tags.version[:2] == (2, 3)
    assert open(str(tmp_path / "a.lrc"), encoding="utf-8").read() == LRC


@requires_ffmpeg
def test_mp3_plain_lyrics_no_sidecar(tmp_path, track):
    from mutagen.id3 import ID3

    path = make_audio(str(tmp_path / "a.mp3"))
    track.lyrics_plain = "just words"
    tag.tag_file(path, track)
    tags = ID3(path)
    assert tags.getall("USLT")[0].text == "just words"
    assert not tags.getall("SYLT")
    assert not os.path.exists(tmp_path / "a.lrc")


@requires_ffmpeg
def test_m4a_tags(tmp_path, track, jpeg_bytes):
    from mutagen.mp4 import MP4

    path = make_audio(str(tmp_path / "a.m4a"))
    track.lyrics_lrc = LRC
    tag.tag_file(path, track)
    audio = MP4(path)
    assert audio["\xa9nam"] == ["Mosquito"]
    assert audio["aART"] == ["PinkPantheress"]
    assert audio["trkn"] == [(3, 9)]
    assert audio["\xa9day"] == ["2025-05-09"]
    assert bytes(audio["covr"][0]) == jpeg_bytes
    assert audio["\xa9lyr"] == [LRC]


@requires_ffmpeg
def test_opus_tags_include_cover(tmp_path, track, jpeg_bytes):
    import base64

    from mutagen.flac import Picture
    from mutagen.oggopus import OggOpus

    path = make_audio(str(tmp_path / "a.opus"))
    tag.tag_file(path, track)
    audio = OggOpus(path)
    assert audio["title"] == ["Mosquito"]
    assert audio["albumartist"] == ["PinkPantheress"]
    assert audio["tracknumber"] == ["3"]
    pic = Picture(base64.b64decode(audio["metadata_block_picture"][0]))
    assert pic.data == jpeg_bytes and pic.type == 3


@requires_ffmpeg
def test_cover_failure_is_not_fatal(tmp_path, track, monkeypatch):
    from mutagen.id3 import ID3

    def boom(url):
        raise OSError("offline")

    monkeypatch.setattr(tag, "_fetch_cover", boom)
    path = make_audio(str(tmp_path / "a.mp3"))
    tag.tag_file(path, track)
    assert not ID3(path).getall("APIC")


def test_non_jpeg_cover_is_converted(monkeypatch):
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (4, 4)).save(buf, "WEBP")

    class Resp:
        content = buf.getvalue()

        def raise_for_status(self):
            pass

    monkeypatch.setattr(tag.requests, "get", lambda url, timeout: Resp())
    data, mime = _real_fetch_cover.__wrapped__("https://example.invalid/x.webp")
    assert mime == "image/jpeg" and data.startswith(b"\xff\xd8")
