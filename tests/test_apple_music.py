import pytest
from conftest import REAL_APPLE_SEARCH

from melograb import apple_music
from melograb.track import Track

ART = "https://is1-ssl.mzstatic.com/image/thumb/Music/v4/ab/cd/source/100x100bb.jpg"


def song(**kw):
    base = {
        "wrapperType": "track", "kind": "song", "trackName": "Husk", "artistName": "Men I Trust",
        "collectionName": "Oncle Jazz", "trackNumber": 7, "trackCount": 24, "discNumber": 1, "discCount": 1,
        "primaryGenreName": "Alternative", "releaseDate": "2019-11-08T08:00:00Z", "trackTimeMillis": 215000,
        "artworkUrl100": ART, "trackExplicitness": "notExplicit",
    }
    base.update(kw)
    return base


@pytest.fixture
def fake_search(monkeypatch):
    calls = []
    responses = {}

    def search(params, timeout=10):
        calls.append(params)
        return responses.get(params["entity"], [])

    monkeypatch.setattr(apple_music, "_search", search)
    return calls, responses


def test_artwork_url_and_album_names():
    assert apple_music.artwork_url(ART).endswith("/source/1200x1200bb.jpg")
    assert apple_music.artwork_url("") == ""
    assert apple_music.clean_album_name("Husk - Single") == "Husk"
    assert apple_music.clean_album_name("Thing - EP") == "Thing"


def test_fills_everything_for_a_bare_track(fake_search):
    calls, responses = fake_search
    responses["song"] = [
        song(trackName="Husk (Live)", trackTimeMillis=300000),  # wrong version: duration way off
        song(collectionName="Oncle Jazz", discCount=2, discNumber=2, trackExplicitness="explicit"),
    ]
    t = Track("Husk", "Men I Trust", duration_ms=214000, cover_url="https://i.ytimg.com/x.jpg")
    assert apple_music.enrich(t)
    assert (t.album, t.album_artist, t.track_number, t.total_tracks) == ("Oncle Jazz", "Men I Trust", 7, 24)
    assert (t.disc_number, t.total_discs, t.genre, t.release_date) == (2, 2, "Alternative", "2019-11-08")
    assert t.cover_url.endswith("1200x1200bb.jpg") and t.cover_is_official and t.explicit
    assert calls[0]["entity"] == "song"


def test_keeps_official_spotify_cover_and_existing_fields(fake_search):
    calls, responses = fake_search
    responses["album"] = [{"collectionName": "Oncle Jazz", "artistName": "Men I Trust", "artworkUrl100": ART,
                           "primaryGenreName": "Alternative", "releaseDate": "2019-11-08T08:00:00Z",
                           "trackCount": 24}]
    t = Track("Husk", "Men I Trust", album="Oncle Jazz", track_number=7, release_date="2019-11-08",
              cover_url="https://i.scdn.co/image/abc", cover_is_official=True)
    assert apple_music.enrich(t)
    assert t.cover_url == "https://i.scdn.co/image/abc"  # Spotify's own album art stays
    assert (t.genre, t.total_tracks, t.album_artist) == ("Alternative", 24, "Men I Trust")
    assert calls[0]["entity"] == "album"  # one lookup per album, not per song


def test_replaces_non_official_cover_for_known_album(fake_search):
    _, responses = fake_search
    responses["album"] = [{"collectionName": "Oncle Jazz", "artistName": "Men I Trust", "artworkUrl100": ART}]
    t = Track("Husk", "Men I Trust", album="Oncle Jazz", track_number=7, cover_url="https://i.ytimg.com/vi/x/hq.jpg")
    apple_music.enrich(t)
    assert t.cover_url.endswith("1200x1200bb.jpg") and t.cover_is_official


def test_different_release_only_gives_genre(fake_search):
    _, responses = fake_search
    responses["song"] = [song(collectionName="Greatest Hits 2000-2020")]
    t = Track("Husk", "Men I Trust", album="Oncle Jazz", track_number=0, cover_url="yt.jpg")
    assert apple_music.enrich(t)
    assert (t.album, t.track_number, t.cover_url, t.genre) == ("Oncle Jazz", 0, "yt.jpg", "Alternative")


def test_rejects_wrong_songs(fake_search):
    _, responses = fake_search
    responses["song"] = [song(trackName="Completely Different"), song(artistName="Someone Else")]
    t = Track("Husk", "Men I Trust")
    assert not apple_music.enrich(t)
    assert t.album == "" and t.cover_url == ""


def test_search_caches_and_survives_errors(monkeypatch):
    import requests

    calls = []

    class Resp:
        status_code = 200

        def json(self):
            return {"results": [song()]}

    def get(url, params=None, headers=None, timeout=None):
        calls.append(params)
        return Resp()

    monkeypatch.setattr(requests, "get", get)
    apple_music._cache.clear()
    params = {"term": "x", "entity": "song"}
    assert REAL_APPLE_SEARCH(params) == REAL_APPLE_SEARCH(params) == [song()]
    assert len(calls) == 1

    def boom(*a, **k):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(requests, "get", boom)
    assert REAL_APPLE_SEARCH({"term": "y", "entity": "song"}) == []


def test_rate_limiter_spaces_calls(monkeypatch):
    now = [0.0]
    slept = []
    monkeypatch.setattr(apple_music.time, "monotonic", lambda: now[0])

    def sleep(s):
        slept.append(s)
        now[0] += s

    monkeypatch.setattr(apple_music.time, "sleep", sleep)
    limiter = apple_music._RateLimiter(2, 60.0)
    limiter.acquire()
    limiter.acquire()
    limiter.acquire()  # third call within the minute has to wait
    assert slept and sum(slept) >= 59.9


def test_release_date_picks_album_over_single(fake_search):
    _, responses = fake_search
    responses["song"] = [
        song(collectionName="Husk - Single", releaseDate="2019-03-21T07:00:00Z"),
        song(collectionName="Oncle Jazz", releaseDate="2019-11-08T08:00:00Z"),
    ]
    t = Track("Husk", "Men I Trust", duration_ms=215000, release_date="2019-11-08")
    apple_music.enrich(t)
    assert t.album == "Oncle Jazz"

    t = Track("Husk", "Men I Trust", duration_ms=215000, release_date="2019-03-21")
    apple_music.enrich(t)
    assert t.album == "Husk"  # " - Single" dropped, like Spotify names it
