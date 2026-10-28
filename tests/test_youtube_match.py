from melograb import youtube_match
from melograb.track import Track


def song(video_id, title, artist, seconds, kind="song", album=None):
    return {
        "videoId": video_id,
        "title": title,
        "artists": [{"name": artist}],
        "duration_seconds": seconds,
        "resultType": kind,
        "album": {"name": album} if album else None,
    }


class FakeYT:
    def __init__(self, songs, videos=()):
        self.songs, self.videos, self.calls = list(songs), list(videos), []

    def search(self, query, filter, limit):
        self.calls.append(filter)
        return self.songs if filter == "songs" else self.videos


def test_prefers_original_over_variants_and_wrong_duration(monkeypatch):
    t = Track(title="Bad Dream - Alternate Version", artist="Flower Face", duration_ms=200_000)
    fake = FakeYT([
        song("live", "Bad Dream (Live)", "Flower Face", 200),
        song("long", "Bad Dream - Alternate Version", "Flower Face", 320),
        song("good", "Bad Dream (Alternate Version)", "Flower Face", 201),
    ])
    monkeypatch.setattr(youtube_match, "client", lambda: fake)
    ranked = youtube_match.rank_matches(t, limit=3)
    assert ranked[0]["videoId"] == "good"
    assert "videos" not in fake.calls  # strong song match, no need for videos


def test_rejects_garbage_and_falls_back_to_videos(monkeypatch):
    t = Track(title="coogie", artist="Dijon", duration_ms=180_000)
    fake = FakeYT(
        [song("junk", "Completely Different", "Other Person", 180)],
        [song("vid", "Dijon - coogie (Official Video)", "Dijon TV", 182, kind="video")],
    )
    monkeypatch.setattr(youtube_match, "client", lambda: fake)
    ranked = youtube_match.rank_matches(t)
    assert [r["videoId"] for r in ranked] == ["vid"]


def test_no_match(monkeypatch):
    t = Track(title="Nothing", artist="Nobody")
    monkeypatch.setattr(youtube_match, "client", lambda: FakeYT([], []))
    assert youtube_match.find_best_match(t) is None


def test_featured_artists_dont_hurt(monkeypatch):
    t = Track(title="Song (feat. Guest)", artist="Main, Guest", duration_ms=100_000)
    fake = FakeYT([song("a", "Song", "Main", 100), song("b", "Song 2", "Main", 100)])
    monkeypatch.setattr(youtube_match, "client", lambda: fake)
    assert youtube_match.rank_matches(t)[0]["videoId"] == "a"
