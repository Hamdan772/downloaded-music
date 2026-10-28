"""Whole pipeline through the CLI with only the network faked: a YouTube Music
playlist link -> real downloader wrapper (fake yt-dlp writing real audio via
ffmpeg) -> real tagging -> template paths -> .m3u8."""
import os

from conftest import make_audio, requires_ffmpeg

from melograb import cli, downloader, env, lyrics, tag, ytmusic_source


class FakeYT:
    def get_playlist(self, playlist_id, limit=None):
        return {"title": "Road Trip", "tracks": [
            {"videoId": "aaaaaaaaaaa", "title": "Husk", "artists": [{"name": "Men I Trust"}],
             "album": {"name": "Oncle Jazz"}, "duration_seconds": 1, "videoType": "MUSIC_VIDEO_TYPE_ATV",
             "thumbnails": [{"url": "https://lh3.example/a=w60-h60", "width": 60}]},
            {"videoId": "bbbbbbbbbbb", "title": "coogie", "artists": [{"name": "Dijon"}],
             "duration_seconds": 1, "videoType": "MUSIC_VIDEO_TYPE_OMV",
             "thumbnails": [{"url": "https://lh3.example/b=w60-h60", "width": 60}]},
        ]}


class FakeYDL:
    def __init__(self, opts):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def download(self, urls):
        video_id = urls[0].rsplit("=", 1)[-1]
        fmt = next(pp["preferredcodec"] for pp in self.opts["postprocessors"] if pp["key"] == "FFmpegExtractAudio")
        make_audio(self.opts["outtmpl"].replace("%(id)s", video_id).replace("%(ext)s", fmt))


@requires_ffmpeg
def test_youtube_playlist_to_tagged_library(tmp_path, monkeypatch, jpeg_bytes, capsys):
    from mutagen.id3 import ID3

    monkeypatch.setattr(ytmusic_source, "client", lambda: FakeYT())
    monkeypatch.setattr(downloader.yt_dlp, "YoutubeDL", FakeYDL)
    monkeypatch.setattr(tag, "_fetch_cover", lambda url: (jpeg_bytes, "image/jpeg"))
    monkeypatch.setattr(lyrics, "fetch_lyrics", lambda t: ("[00:00.50]la", "la"))
    monkeypatch.setattr(env, "setup_logging", lambda: str(tmp_path / "log"))
    monkeypatch.setattr(env, "environment_warnings", lambda: [])

    out = tmp_path / "lib"
    cli.main(["https://music.youtube.com/playlist?list=PLroad", "-o", str(out),
              "-t", "{playlist}/{position:02} {artist} - {title}"])

    husk = out / "Road Trip" / "01 Men I Trust - Husk.mp3"
    coogie = out / "Road Trip" / "02 Dijon - coogie.mp3"
    assert husk.exists() and coogie.exists()
    tags = ID3(str(husk))
    assert str(tags["TALB"]) == "Oncle Jazz" and str(tags["TIT2"]) == "Husk"
    assert tags.getall("SYLT") and tags.getall("APIC")
    assert "music.youtube.com/watch?v=aaaaaaaaaaa" in str(tags.getall("COMM")[0])
    assert (out / "Road Trip" / "01 Men I Trust - Husk.lrc").exists()
    m3u = (out / "Road Trip.m3u8").read_text(encoding="utf-8").splitlines()
    assert m3u[2] == "Road Trip/01 Men I Trust - Husk.mp3" and m3u[4] == "Road Trip/02 Dijon - coogie.mp3"
    assert not [p for p in os.listdir(out / "Road Trip") if p.startswith(".melograb-")]
    assert "2 downloaded" in capsys.readouterr().out

    # Second run: everything already there.
    cli.main(["https://music.youtube.com/playlist?list=PLroad", "-o", str(out),
              "-t", "{playlist}/{position:02} {artist} - {title}"])
    assert "0 downloaded, 2 already present" in capsys.readouterr().out
