import pytest

from melograb import ytmusic_source as yts


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://youtu.be/dQw4w9WgXcQ?t=3", ("video", "dQw4w9WgXcQ")),
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", ("video", "dQw4w9WgXcQ")),
        ("https://music.youtube.com/watch?v=dQw4w9WgXcQ&list=RDAMVMdQw4w9WgXcQ", ("video", "dQw4w9WgXcQ")),
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLabc", ("video", "dQw4w9WgXcQ")),
        ("https://music.youtube.com/playlist?list=PLabc", ("playlist", "PLabc")),
        ("https://www.youtube.com/playlist?list=OLAK5uy_x", ("playlist", "OLAK5uy_x")),
        ("https://music.youtube.com/browse/MPREb_abc", ("album", "MPREb_abc")),
        ("https://music.youtube.com/browse/VLPLabc", ("playlist", "PLabc")),
        ("https://www.youtube.com/shorts/dQw4w9WgXcQ", ("video", "dQw4w9WgXcQ")),
    ],
)
def test_parse(url, expected):
    assert yts.parse_youtube_url(url) == expected


def test_parse_rejects():
    with pytest.raises(ValueError):
        yts.parse_youtube_url("https://www.youtube.com/@channel")


@pytest.mark.parametrize(
    "title,author,expected",
    [
        ("Mac DeMarco - Chamber Of Reflection (Official Video)", "Captured Tracks",
         ("Chamber Of Reflection", "Mac DeMarco")),
        ("Husk [Official Audio]", "Men I Trust", ("Husk", "Men I Trust")),
        ("Song", "Artist - Topic", ("Song", "Artist")),
        ("Song (Lyrics)", "ArtistVEVO", ("Song", "Artist")),
    ],
)
def test_clean_video_title(title, author, expected):
    assert yts.clean_video_title(title, author) == expected


class FakeYT:
    def get_playlist(self, playlist_id, limit=None):
        return {
            "title": "Chill",
            "thumbnails": [{"url": "pl=w60-h60", "width": 60}],
            "tracks": [
                {"videoId": "v1", "title": "Husk", "artists": [{"name": "Men I Trust"}],
                 "album": {"name": "Oncle Jazz"}, "duration_seconds": 200, "isExplicit": False,
                 "videoType": "MUSIC_VIDEO_TYPE_ATV", "thumbnails": [{"url": "t=w60-h60", "width": 60}]},
                {"videoId": "v2", "title": "Gone", "artists": [], "isAvailable": False},
                {"videoId": None, "title": "Deleted"},
                {"videoId": "v3", "title": "Clip", "artists": [{"name": "X"}], "videoType": "MUSIC_VIDEO_TYPE_OMV"},
            ],
        }

    def get_album_browse_id(self, playlist_id):
        return "MPREb_1"

    def get_album(self, browse_id):
        return {
            "title": "Oncle Jazz", "artists": [{"name": "Men I Trust"}], "year": "2019",
            "thumbnails": [{"url": "al=w60-h60", "width": 60}],
            "tracks": [{"videoId": "a1", "title": "Tailwhip", "artists": [], "trackNumber": 1, "duration_seconds": 180},
                       {"videoId": "a2", "title": "Husk", "artists": [{"name": "Men I Trust"}], "trackNumber": 2}],
        }

    def get_song(self, video_id):
        return {"videoDetails": {"title": "Dijon - coogie (Official Video)", "author": "Dijon", "lengthSeconds": "182",
                                 "musicVideoType": "MUSIC_VIDEO_TYPE_OMV",
                                 "thumbnail": {"thumbnails": [{"url": "v=w60-h60", "width": 60}]}}}

    def search(self, query, filter, limit):
        if filter == "songs":
            return [{"videoId": "s1", "title": "Chamber Of Reflection", "artists": [{"name": "Mac DeMarco"}],
                     "album": {"name": "Salad Days"}, "resultType": "song", "duration_seconds": 231}]
        return []


@pytest.fixture(autouse=True)
def fake_client(monkeypatch):
    monkeypatch.setattr(yts, "client", lambda: FakeYT())


def test_playlist():
    coll = yts.fetch_collection("https://music.youtube.com/playlist?list=PLx")
    assert coll.name == "Chill" and coll.cover_url == "pl=w600-h600"
    assert [(t.video_id, t.video_type) for t in coll.tracks] == [("v1", "song"), ("v3", "video")]
    first = coll.tracks[0]
    assert (first.album, first.duration_ms, first.cover_url, first.position) == ("Oncle Jazz", 200_000,
                                                                                 "t=w600-h600", 1)


def test_album_playlist_uses_album_view():
    coll = yts.fetch_collection("https://music.youtube.com/playlist?list=OLAK5uy_abc")
    assert coll.kind == "youtube-album"
    a, b = coll.tracks
    assert (a.artist, a.album, a.track_number, a.total_tracks, a.release_date) == (
        "Men I Trust", "Oncle Jazz", 1, 2, "2019")
    assert a.cover_url == "al=w600-h600" and a.video_type == "song"


def test_video():
    (t,) = yts.fetch_collection("https://youtu.be/dQw4w9WgXcQ").tracks
    assert (t.title, t.artist, t.duration_ms, t.video_type) == ("coogie", "Dijon", 182_000, "video")


def test_search():
    coll = yts.search("mac demarco chamber")
    (t,) = coll.tracks
    assert (t.title, t.artist, t.album, t.video_id) == ("Chamber Of Reflection", "Mac DeMarco", "Salad Days", "s1")
