import pytest

from melograb import public_source
from melograb.public_source import parse_spotify_url

ID = "37i9dQZF1DXcxvFzl58uP7"


@pytest.mark.parametrize(
    "url,expected",
    [
        (f"https://open.spotify.com/playlist/{ID}", ("playlist", ID)),
        (f"https://open.spotify.com/playlist/{ID}?si=abc123", ("playlist", ID)),
        (f"https://open.spotify.com/intl-de/album/{ID}", ("album", ID)),
        (f"https://open.spotify.com/embed/playlist/{ID}", ("playlist", ID)),
        (f"open.spotify.com/album/{ID}", ("album", ID)),
        (f"  spotify:playlist:{ID}  ", ("playlist", ID)),
        (f"https://open.spotify.com/track/{ID}?si=x", ("track", ID)),
        (f"spotify:artist:{ID}", ("artist", ID)),
    ],
)
def test_parse_spotify_url(url, expected):
    assert parse_spotify_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/playlist/" + ID,
        f"https://open.spotify.com/episode/{ID}",
        "https://open.spotify.com/playlist/short",
        "spotify:show:" + ID,
    ],
)
def test_parse_spotify_url_rejects(url):
    with pytest.raises(ValueError):
        parse_spotify_url(url)


class _Resp:
    def __init__(self, text, status=200):
        self.text = text
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise public_source.requests.HTTPError(str(self.status_code))


def test_playlist_tracks_get_their_own_cover_and_date(monkeypatch):
    playlist = {
        "name": "Bedroom Pop",
        "coverArt": {"sources": [{"url": "playlist-small", "width": 60}, {"url": "playlist-big", "width": 640}]},
        "trackList": [
            {"title": "Mosquito", "subtitle": "PinkPantheress", "uri": "spotify:track:" + "T" * 22, "duration": 1000},
            {"title": "No Embed", "subtitle": "Someone", "uri": "spotify:track:" + "U" * 22, "duration": 2000},
        ],
    }
    # Shape of a real /embed/track entity (no album name, but the album's art).
    track_embed = {
        "name": "Mosquito",
        "artists": [{"name": "PinkPantheress"}],
        "releaseDate": {"isoString": "2025-05-09T00:00:00Z"},
        "visualIdentity": {"image": [{"url": "album-300", "maxWidth": 300}, {"url": "album-640", "maxWidth": 640}]},
    }

    def fake_entity(kind, item_id, timeout=20):
        if kind == "playlist":
            return playlist
        if item_id == "T" * 22:
            return track_embed
        raise ValueError("embed failed")

    monkeypatch.setattr(public_source, "_get_entity", fake_entity)
    progress = []
    coll = public_source.fetch_collection(
        f"https://open.spotify.com/playlist/{ID}", progress=lambda d, t: progress.append((d, t))
    )
    first, second = coll.tracks
    assert coll.cover_url == "playlist-big"
    assert (first.cover_url, first.cover_is_official, first.release_date) == ("album-640", True, "2025-05-09")
    assert first.track_number == 0 and first.position == 1  # no fake track number from the playlist position
    # No embed: never the playlist's cover (the album art is looked up later).
    assert (second.cover_url, second.cover_is_official) == ("", False)
    assert progress[-1] == (2, 2)


def test_album_tracks_numbered_and_dated(monkeypatch):
    album = {
        "name": "LP",
        "subtitle": "Band",
        "releaseDate": {"isoString": "2020-01-02T00:00:00Z"},
        "trackList": [{"title": "One", "subtitle": "Band"}, {"title": "Two", "subtitle": "Band, Guest"}],
    }
    monkeypatch.setattr(public_source, "_get_entity", lambda *a, **k: album)
    coll = public_source.fetch_collection(f"https://open.spotify.com/album/{ID}")
    assert [(t.track_number, t.total_tracks, t.album, t.release_date) for t in coll.tracks] == [
        (1, 2, "LP", "2020-01-02"),
        (2, 2, "LP", "2020-01-02"),
    ]


def test_single_track_link(monkeypatch):
    track_entity = {
        "name": "Husk",
        "artists": [{"name": "Men I Trust"}],
        "duration": 204_651,
        "isExplicit": True,
        "releaseDate": {"isoString": "2019-11-08T00:00:00Z"},
        "visualIdentity": {"image": [{"url": "album-cover", "maxWidth": 640}]},
    }
    monkeypatch.setattr(public_source, "_get_entity", lambda kind, item_id, timeout=20: track_entity)
    coll = public_source.fetch_collection(f"https://open.spotify.com/track/{ID}")
    (t,) = coll.tracks
    assert coll.kind == "track" and coll.name == "Men I Trust - Husk"
    assert (t.title, t.artist, t.release_date, t.duration_ms) == ("Husk", "Men I Trust", "2019-11-08", 204_651)
    assert t.explicit and t.cover_url == "album-cover" and t.cover_is_official and t.spotify_id == ID


def test_artist_link_is_top_tracks(monkeypatch):
    artist = {"name": "Dijon", "trackList": [{"title": "coogie", "subtitle": "Dijon"}]}
    monkeypatch.setattr(public_source, "_get_entity", lambda *a, **k: artist)
    coll = public_source.fetch_collection(f"https://open.spotify.com/artist/{ID}")
    assert coll.name == "Dijon - Top Tracks"
    assert coll.tracks[0].track_number == 0 and coll.tracks[0].playlist == "Dijon - Top Tracks"


def test_short_link_resolution(monkeypatch):
    class Page:
        url = "https://spotify.app.link/abc?_p=1"
        text = f'<a href="https://open.spotify.com/playlist/{ID}?si=1">open</a>'

    class FakeSession:
        def get(self, url, timeout=None):
            return Page()

    monkeypatch.setattr(public_source, "_http", lambda: FakeSession())
    assert public_source.resolve_short_link("spotify.link/abc") == f"https://open.spotify.com/playlist/{ID}"
    assert public_source.is_spotify_link("https://spotify.link/abc")
