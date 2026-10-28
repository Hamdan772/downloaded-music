import os

import pytest

from melograb import downloader


@pytest.mark.parametrize(
    "raw,expected_fragment",
    [
        ("ERROR: [youtube] abc: Requested format is not available. Use --list-formats", "yt-dlp"),
        ("ERROR: Postprocessing: ffprobe and ffmpeg not found. Please install", "ffmpeg is not installed"),
        ("ERROR: [youtube] abc: Sign in to confirm you're not a bot", "bot verification"),
        ("ERROR: [youtube] abc: Video unavailable", "unavailable"),
        ("\x1b[0;31mERROR:\x1b[0m something odd\nsecond line", "something odd"),
    ],
)
def test_friendly_error(raw, expected_fragment):
    msg = downloader.friendly_error(raw)
    assert expected_fragment in msg
    assert "\n" not in msg and "\x1b" not in msg


def test_does_not_force_youtube_player_clients(tmp_path, monkeypatch):
    monkeypatch.setattr(downloader.env, "available_js_runtimes", lambda: ["node"])
    opts = downloader._ydl_opts(str(tmp_path), "m4a")
    assert "extractor_args" not in opts
    assert opts["js_runtimes"] == {"node": {}}
    assert opts["format"].startswith("bestaudio[ext=m4a]")


class FakeYDL:
    produce = "mp3"

    def __init__(self, opts):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def download(self, urls):
        video_id = urls[0].rsplit("=", 1)[-1]
        out = self.opts["outtmpl"].replace("%(id)s", video_id).replace("%(ext)s", self.produce)
        open(out, "wb").write(b"audio")


def test_download_moves_file_and_cleans_temp(tmp_path, monkeypatch):
    monkeypatch.setattr(downloader.yt_dlp, "YoutubeDL", FakeYDL)
    stem = "100% Real - Ünïcödé"
    path = downloader.download_audio("vid123", str(tmp_path / "Album"), stem, "mp3")
    assert path == str(tmp_path / "Album" / f"{stem}.mp3")
    assert open(path, "rb").read() == b"audio"
    assert os.listdir(tmp_path / "Album") == [f"{stem}.mp3"]  # temp dir removed


def test_download_reports_failed_conversion(tmp_path, monkeypatch):
    class WrongExt(FakeYDL):
        produce = "webm"

    monkeypatch.setattr(downloader.yt_dlp, "YoutubeDL", WrongExt)
    with pytest.raises(downloader.DownloadError, match="ffmpeg"):
        downloader.download_audio("vid", str(tmp_path), "x", "mp3")
    assert os.listdir(tmp_path) == []


def test_download_wraps_ytdlp_errors(tmp_path, monkeypatch):
    class Failing(FakeYDL):
        def download(self, urls):
            raise downloader.yt_dlp.utils.DownloadError("ERROR: [youtube] x: Video unavailable")

    monkeypatch.setattr(downloader.yt_dlp, "YoutubeDL", Failing)
    with pytest.raises(downloader.DownloadError, match="unavailable"):
        downloader.download_audio("vid", str(tmp_path), "x", "mp3")


def test_rejects_unknown_format(tmp_path):
    with pytest.raises(ValueError):
        downloader.download_audio("vid", str(tmp_path), "x", "flac")


def test_sponsorblock_and_quality_opts(tmp_path):
    opts = downloader._ydl_opts(str(tmp_path), "mp3", quality="192", remove_non_music=True)
    keys = [pp["key"] for pp in opts["postprocessors"]]
    assert keys == ["SponsorBlock", "FFmpegExtractAudio", "ModifyChapters"]
    assert opts["postprocessors"][1]["preferredquality"] == "192"
    plain = downloader._ydl_opts(str(tmp_path), "mp3")
    assert [pp["key"] for pp in plain["postprocessors"]] == ["FFmpegExtractAudio"]
    assert plain["postprocessors"][0]["preferredquality"] == "0"


def test_real_ytdlp_accepts_our_options(tmp_path):
    for fmt in downloader.FORMATS:
        with downloader.yt_dlp.YoutubeDL(downloader._ydl_opts(str(tmp_path), fmt, "256", True)):
            pass


def test_sponsorblock_outage_falls_back_to_uncut(tmp_path, monkeypatch):
    attempts = []

    class Flaky(FakeYDL):
        def download(self, urls):
            attempts.append([pp["key"] for pp in self.opts["postprocessors"]])
            if len(attempts) == 1:
                raise downloader.yt_dlp.utils.DownloadError("ERROR: Unable to communicate with SponsorBlock API")
            super().download(urls)

    monkeypatch.setattr(downloader.yt_dlp, "YoutubeDL", Flaky)
    path = downloader.download_audio("vid", str(tmp_path), "x", "mp3", remove_non_music=True)
    assert os.path.exists(path)
    assert attempts == [["SponsorBlock", "FFmpegExtractAudio", "ModifyChapters"], ["FFmpegExtractAudio"]]


def test_rejects_unknown_quality(tmp_path):
    with pytest.raises(ValueError):
        downloader.download_audio("vid", str(tmp_path), "x", "mp3", quality="999")
