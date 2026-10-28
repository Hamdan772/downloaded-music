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
    ],
)
def test_parse_spotify_url(url, expected):
    assert parse_spotify_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/playlist/" + ID,
        f"https://open.spotify.com/track/{ID}",
        "https://open.spotify.com/playlist/short",
        "spotify:artist:" + ID,
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
    # Un-enrichable track: no fake track number from its playlist position,
    # playlist cover only as a fallback.
    assert (second.album, second.track_number, second.cover_url) == ("", 0, "playlist-big")
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
