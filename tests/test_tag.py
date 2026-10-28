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
    track.genre, track.disc_number, track.total_discs = "Pop", 1, 2
    tag.tag_file(path, track, lyrics="synced+lrc")
    tags = ID3(path)
    assert str(tags["TIT2"]) == "Mosquito"
    assert str(tags["TCON"]) == "Pop" and str(tags["TPOS"]) == "1/2"
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


@requires_ffmpeg
def test_source_comment_and_explicit(tmp_path, track):
    from mutagen.id3 import ID3
    from mutagen.mp4 import MP4

    track.video_id, track.explicit = "abcdefghijk", True
    mp3 = make_audio(str(tmp_path / "a.mp3"))
    tag.tag_file(mp3, track)
    tags = ID3(mp3)
    assert tags.getall("COMM")[0].text == ["Source: https://music.youtube.com/watch?v=abcdefghijk"]
    assert tags.getall("TXXX:ITUNESADVISORY")[0].text == ["1"]
    m4a = make_audio(str(tmp_path / "a.m4a"))
    tag.tag_file(m4a, track)
    assert MP4(m4a)["rtng"] == [1]


@requires_ffmpeg
@pytest.mark.parametrize("ext", [".mp3", ".m4a", ".opus"])
def test_embed_lyrics_keeps_other_tags(tmp_path, track, ext):
    from mutagen import File

    path = make_audio(str(tmp_path / f"a{ext}"))
    tag.tag_file(path, track)
    assert not tag.has_lyrics(path)
    tag.embed_lyrics(path, LRC, "plain")
    assert tag.has_lyrics(path)
    assert File(path, easy=True)["title"] == ["Mosquito"]
    assert os.path.exists(tmp_path / "a.lrc")


@requires_ffmpeg
def test_lyrics_modes(tmp_path, track):
    from mutagen.id3 import ID3
    from mutagen.mp4 import MP4

    track.lyrics_lrc = "[ar:Someone]\n[00:01.00]first line\n[00:02.50]second"
    track.lyrics_plain = ""

    mp3 = make_audio(str(tmp_path / "default.mp3"))
    tag.tag_file(mp3, track)  # default: timestamped, inside the song only
    tags = ID3(mp3)
    assert tags.getall("SYLT")[0].text[0] == ("first line", 1000)
    assert "[00:01.00]first line" in tags.getall("USLT")[0].text
    assert not os.path.exists(tmp_path / "default.lrc")

    plain = make_audio(str(tmp_path / "plain.m4a"))
    tag.tag_file(plain, track, lyrics="plain")
    assert MP4(plain)["\xa9lyr"] == ["first line\nsecond"]

    off = make_audio(str(tmp_path / "off.mp3"))
    tag.tag_file(off, track, lyrics="off")
    assert not ID3(off).getall("USLT") and not ID3(off).getall("SYLT")

    with pytest.raises(ValueError):
        tag.tag_file(off, track, lyrics="karaoke")


@requires_ffmpeg
def test_genre_and_disc_in_m4a_and_opus(tmp_path, track):
    from mutagen.mp4 import MP4
    from mutagen.oggopus import OggOpus

    track.genre, track.disc_number, track.total_discs = "Indie", 2, 2
    m4a = make_audio(str(tmp_path / "a.m4a"))
    tag.tag_file(m4a, track)
    assert MP4(m4a)["\xa9gen"] == ["Indie"] and MP4(m4a)["disk"] == [(2, 2)]
    opus = make_audio(str(tmp_path / "a.opus"))
    tag.tag_file(opus, track)
    audio = OggOpus(opus)
    assert audio["genre"] == ["Indie"] and audio["discnumber"] == ["2"] and audio["disctotal"] == ["2"]


def test_square_cover_crops_letterboxed_thumbnails():
    import io

    from PIL import Image

    # 480x360 thumbnail of a 16:9 video: black bars top and bottom.
    img = Image.new("RGB", (480, 360), (0, 0, 0))
    img.paste(Image.new("RGB", (480, 270), (200, 50, 50)), (0, 45))
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    data, mime = tag.square_cover(buf.getvalue())
    out = Image.open(io.BytesIO(data))
    assert mime == "image/jpeg" and out.size[0] == out.size[1] == 270
    r, g, b = out.getpixel((5, 5))
    assert r > 150 and g < 100  # no black bar left


def test_square_cover_keeps_square_jpeg_untouched(jpeg_bytes):
    assert tag.square_cover(jpeg_bytes) == (jpeg_bytes, "image/jpeg")


def test_strip_timestamps():
    assert tag.strip_timestamps("[ti:x]\n[00:01.00]a\n[00:02.00][00:03.00]b\n") == "a\nb"
