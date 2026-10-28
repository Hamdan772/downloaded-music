import os
import threading

import pytest

from melograb import downloader, lyrics, pipeline, tag, youtube_match
from melograb.track import Track


@pytest.fixture
def fakes(monkeypatch):
    state = {"downloads": [], "fail_ids": set(), "candidates": None}

    def rank(track, limit=3):
        if state["candidates"] is not None:
            return state["candidates"]
        return [
            {"videoId": f"{track.title}-1", "album": {"name": "YT Album"}, "thumbnails": [{"url": "x=w120-h120"}]},
            {"videoId": f"{track.title}-2"},
        ]

    def download(video_id, out_dir, stem, fmt, quality="best", remove_non_music=False, cookies_browser=""):
        state["downloads"].append(video_id)
        state["browser"] = cookies_browser
        state.setdefault("calls", []).append((video_id, quality, remove_non_music))
        if video_id in state["fail_ids"]:
            raise downloader.DownloadError("video is unavailable")
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, f"{stem}.{fmt}")
        open(path, "wb").close()
        return path

    monkeypatch.setattr(youtube_match, "rank_matches", rank)
    monkeypatch.setattr(downloader, "download_audio", download)
    monkeypatch.setattr(tag, "tag_file", lambda path, track: None)
    monkeypatch.setattr(lyrics, "fetch_lyrics", lambda track: ("[00:01.00]hi", "hi"))
    return state


def test_run_basic_grouping_and_order(tmp_path, fakes):
    tracks = [Track("A", "X", album="Alb"), Track("B", "Y")]
    seen = []
    opts = pipeline.Options(workers=2)
    results = pipeline.run(tracks, str(tmp_path), opts, on_result=lambda i, r: seen.append(i))
    assert [r.status for r in results] == ["done", "done"]
    assert results[0].path == str(tmp_path / "Alb" / "X - A.mp3")
    # No Spotify album -> YouTube Music's album name and a bigger thumbnail
    assert results[1].path == str(tmp_path / "YT Album" / "Y - B.mp3")
    assert tracks[1].cover_url == "x=w600-h600"
    assert tracks[1].lyrics_lrc == "[00:01.00]hi"
    assert sorted(seen) == [0, 1]


def test_falls_back_to_second_candidate(tmp_path, fakes):
    fakes["fail_ids"] = {"A-1"}
    (r,) = pipeline.run([Track("A", "X", album="Alb")], str(tmp_path))
    assert r.status == "done"
    assert fakes["downloads"] == ["A-1", "A-2"]


def test_reports_reason_when_all_candidates_fail(tmp_path, fakes):
    fakes["fail_ids"] = {"A-1", "A-2"}
    (r,) = pipeline.run([Track("A", "X", album="Alb")], str(tmp_path))
    assert (r.status, r.error, r.ok) == ("failed", "video is unavailable", False)


def test_no_match(tmp_path, fakes):
    fakes["candidates"] = []
    (r,) = pipeline.run([Track("A", "X")], str(tmp_path))
    assert r.status == "failed" and "no matching song" in r.error


def test_skips_existing_and_duplicates(tmp_path, fakes):
    os.makedirs(tmp_path / "Alb")
    open(tmp_path / "Alb" / "X - A.mp3", "wb").close()
    tracks = [Track("A", "X", album="Alb"), Track("B", "X", album="Alb"), Track("B", "X", album="Alb")]
    results = pipeline.run(tracks, str(tmp_path), pipeline.Options(workers=1))
    assert [r.status for r in results] == ["skipped", "done", "duplicate"]
    assert fakes["downloads"] == ["B-1"]


def test_cancel_skips_queued(tmp_path, fakes):
    cancel = threading.Event()
    cancel.set()
    results = pipeline.run([Track("A", "X")], str(tmp_path), cancel=cancel)
    assert results[0].status == "cancelled" and not fakes["downloads"]


def test_unexpected_exception_becomes_result(tmp_path, fakes, monkeypatch):
    def boom(track, limit=3):
        raise RuntimeError("network down")

    monkeypatch.setattr(youtube_match, "rank_matches", boom)
    (r,) = pipeline.run([Track("A", "X")], str(tmp_path))
    assert r.status == "failed" and r.error == "network down"


def test_write_m3u_and_summary(tmp_path, fakes):
    tracks = [Track("A", "X", album="Alb", duration_ms=61_400), Track("B", "Y", album="Alb")]
    fakes["fail_ids"] = {"B-1", "B-2"}
    results = pipeline.run(tracks, str(tmp_path))
    m3u = pipeline.write_m3u(results, str(tmp_path), "My/Playlist")
    assert os.path.basename(m3u) == "My-Playlist.m3u8"
    assert open(m3u, encoding="utf-8").read() == "#EXTM3U\n#EXTINF:61,X - A\nAlb/X - A.mp3\n"
    assert pipeline.summarize_failures(results) == [("video is unavailable", 1)]


def test_known_video_skips_matching_and_sponsorblock_only_for_videos(tmp_path, fakes, monkeypatch):
    def no_search(track, limit=3):
        raise AssertionError("should not search")

    monkeypatch.setattr(youtube_match, "rank_matches", no_search)
    tracks = [Track("A", "X", album="Alb", video_id="vidA", video_type="song"),
              Track("B", "Y", album="Alb", video_id="vidB", video_type="video")]
    results = pipeline.run(tracks, str(tmp_path), pipeline.Options(quality="320"))
    assert [r.status for r in results] == ["done", "done"]
    assert sorted(fakes["calls"]) == [("vidA", "320", False), ("vidB", "320", True)]


def test_sponsorblock_can_be_disabled(tmp_path, fakes):
    pipeline.run([Track("A", "X", album="Alb", video_id="v", video_type="video")], str(tmp_path),
                 pipeline.Options(remove_non_music=False))
    assert fakes["calls"] == [("v", "best", False)]


def test_template_and_replace_existing(tmp_path, fakes):
    opts = pipeline.Options(template="{album_artist}/{album}/{track:02} {title}", fmt="m4a")
    track = Track("Song", "X", album="Alb", album_artist="X", track_number=4)
    target = tmp_path / "X" / "Alb" / "04 Song.m4a"
    os.makedirs(target.parent)
    target.write_bytes(b"old")
    (r,) = pipeline.run([track], str(tmp_path), opts)
    assert r.status == "skipped" and r.path == str(target)

    track.replace_existing = True
    (r,) = pipeline.run([track], str(tmp_path), opts)
    assert r.status == "done" and r.path == str(target) and target.read_bytes() == b""


def test_successful_match_is_remembered(tmp_path, fakes):
    track = Track("A", "X", album="Alb")
    (r,) = pipeline.run([track], str(tmp_path))
    assert (r.video_id, track.video_id, track.video_type) == ("A-1", "A-1", "video")


def test_invalid_options_rejected(tmp_path, fakes):
    with pytest.raises(ValueError):
        pipeline.run([Track("A", "X")], str(tmp_path), pipeline.Options(template="{bogus}"))
    with pytest.raises(ValueError):
        pipeline.run([Track("A", "X")], str(tmp_path), pipeline.Options(quality="999"))


def test_preview(fakes):
    fakes["candidates"] = [{"videoId": "v", "title": "T", "artists": [{"name": "A"}], "_score": 90.0}]
    (m,) = pipeline.preview([Track("T", "A")])
    assert m["display"] == "A - T" and m["videoId"] == "v"
    fakes["candidates"] = []
    assert pipeline.preview([Track("T", "A")]) == [None]


def test_browser_sign_in_is_passed_and_bot_checks_dont_retry(tmp_path, fakes, monkeypatch):
    pipeline.run([Track("A", "X", album="Alb")], str(tmp_path), pipeline.Options(cookies_browser="safari"))
    assert fakes["browser"] == "safari"

    def blocked(video_id, *a, **k):
        fakes["downloads"].append(video_id)
        raise downloader.DownloadError("YouTube is asking for bot verification -- pick the browser")

    monkeypatch.setattr(downloader, "download_audio", blocked)
    fakes["downloads"].clear()
    (r,) = pipeline.run([Track("B", "Y", album="Alb")], str(tmp_path))
    assert r.status == "failed" and fakes["downloads"] == ["B-1"]  # second candidate would fail the same way
    with pytest.raises(ValueError):
        pipeline.Options(cookies_browser="netscape").validate()
