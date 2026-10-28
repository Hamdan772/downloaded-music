import pytest

from melograb import public_source
from melograb.public_source import _parse_meta, parse_spotify_url

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


def test_parse_meta_any_attribute_order_and_entities():
    page = """
    <meta content="https://open.spotify.com/album/AAAAAAAAAAAAAAAAAAAAAA" name="music:album"/>
    <meta name='music:album:track' content='7'>
    <meta property="og:title" content="Rock &amp; Roll">
    """
    meta = _parse_meta(page)
    assert meta["music:album"].endswith("AAAAAAAAAAAAAAAAAAAAAA")
    assert meta["music:album:track"] == "7"
    assert meta["og:title"] == "Rock & Roll"


class _Resp:
    def __init__(self, text, status=200):
        self.text = text
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise public_source.requests.HTTPError(str(self.status_code))


def test_playlist_tracks_get_their_own_album_and_cover(monkeypatch):
    album_id = "B" * 22
    playlist = {
        "name": "Bedroom Pop",
        "coverArt": {"sources": [{"url": "playlist-small", "width": 60}, {"url": "playlist-big", "width": 640}]},
        "trackList": [
            {"title": "Mosquito", "subtitle": "PinkPantheress", "uri": "spotify:track:" + "T" * 22, "duration": 1000},
            {"title": "No Album", "subtitle": "Someone", "uri": "spotify:track:" + "U" * 22, "duration": 2000},
        ],
    }
    album = {
        "name": "Fancy That",
        "subtitle": "PinkPantheress",
        "visualIdentity": {"image": [{"url": "album-big", "maxWidth": 640}]},
        "trackList": [{}] * 9,
        "releaseDate": {"isoString": "2025-05-09T00:00:00Z"},
    }

    def fake_entity(kind, item_id, timeout=20):
        return playlist if kind == "playlist" else album

    class FakeSession:
        def get(self, url, timeout=None):
            if url.endswith("T" * 22):
                return _Resp(
                    f'<meta name="music:album" content="https://open.spotify.com/album/{album_id}">'
                    '<meta name="music:album:track" content="3">'
                )
            return _Resp("<html>no meta</html>")

    monkeypatch.setattr(public_source, "_get_entity", fake_entity)
    monkeypatch.setattr(public_source, "_http", lambda: FakeSession())
    public_source._album_cache.clear()

    progress = []
    coll = public_source.fetch_collection(
        f"https://open.spotify.com/playlist/{ID}", progress=lambda d, t: progress.append((d, t))
    )
    first, second = coll.tracks
    assert coll.cover_url == "playlist-big"
    assert (first.album, first.track_number, first.total_tracks) == ("Fancy That", 3, 9)
    assert first.cover_url == "album-big"  # not the playlist's cover
    assert first.album_artist == "PinkPantheress"
    assert first.release_date == "2025-05-09"
    assert first.position == 1
    assert first.cover_is_official
    # Un-enrichable track: no fake track number from its playlist position,
    # and never the playlist's cover (the album art is looked up later).
    assert (second.album, second.track_number, second.cover_url) == ("", 0, "")
    assert not second.cover_is_official
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
        "duration": 200_000,
        "isExplicit": True,
        "visualIdentity": {"image": [{"url": "track-cover", "maxWidth": 300}]},
    }
    album = {"name": "Oncle Jazz", "subtitle": "Men I Trust", "trackList": [{}] * 24,
             "visualIdentity": {"image": [{"url": "album-cover", "maxWidth": 640}]}}

    def fake_entity(kind, item_id, timeout=20):
        return track_entity if kind == "track" else album

    class FakeSession:
        def get(self, url, timeout=None):
            return _Resp(
                f'<meta name="music:album" content="https://open.spotify.com/album/{"C" * 22}">'
                '<meta name="music:album:track" content="11">'
            )

    monkeypatch.setattr(public_source, "_get_entity", fake_entity)
    monkeypatch.setattr(public_source, "_http", lambda: FakeSession())
    public_source._album_cache.clear()
    coll = public_source.fetch_collection(f"https://open.spotify.com/track/{ID}")
    (t,) = coll.tracks
    assert coll.kind == "track" and coll.name == "Men I Trust - Husk"
    assert (t.title, t.artist, t.album, t.track_number, t.total_tracks) == ("Husk", "Men I Trust", "Oncle Jazz", 11, 24)
    assert t.explicit and t.cover_url == "album-cover" and t.spotify_id == ID


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
