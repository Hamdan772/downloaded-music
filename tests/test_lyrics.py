from melograb import lyrics
from melograb.track import Track


class Resp:
    def __init__(self, status, data):
        self.status_code, self._data = status, data

    def json(self):
        return self._data


def test_exact_hit(monkeypatch):
    monkeypatch.setattr(
        lyrics.requests, "get",
        lambda url, **kw: Resp(200, {"syncedLyrics": "[00:01.00]a", "plainLyrics": "a"}),
    )
    assert lyrics.fetch_lyrics(Track("T", "A", duration_ms=100_000)) == ("[00:01.00]a", "a")


def test_search_prefers_closest_synced(monkeypatch):
    def get(url, params=None, **kw):
        if url.endswith("/get"):
            return Resp(404, {})
        return Resp(200, [
            {"duration": 300, "syncedLyrics": "[00:01.00]wrong edit"},
            {"duration": 101, "plainLyrics": "plain only"},
            {"duration": 99, "syncedLyrics": "[00:01.00]right", "plainLyrics": "right"},
        ])

    monkeypatch.setattr(lyrics.requests, "get", get)
    assert lyrics.fetch_lyrics(Track("T", "A", duration_ms=100_000)) == ("[00:01.00]right", "right")


def test_network_errors_give_empty(monkeypatch):
    def get(*a, **kw):
        raise lyrics.requests.ConnectionError("offline")

    monkeypatch.setattr(lyrics.requests, "get", get)
    assert lyrics.fetch_lyrics(Track("T", "A")) == ("", "")
    assert lyrics.fetch_lrc(Track("T", "A")) == ""
