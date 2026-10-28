import pytest

from melograb import csv_source, public_source, sources, ytmusic_source
from melograb.track import Collection, Track

SP = "https://open.spotify.com/playlist/" + "A" * 22


def test_split_inputs():
    text = f"{SP}  https://music.youtube.com/playlist?list=PL1\nmac demarco - chamber of reflection\n\n"
    assert sources.split_inputs(text) == [SP, "https://music.youtube.com/playlist?list=PL1",
                                          "mac demarco - chamber of reflection"]


def test_describe(tmp_path):
    csv = tmp_path / "x.csv"
    csv.write_text("Title,Artist\nA,B\n")
    assert sources.describe(SP) == "spotify"
    assert sources.describe("https://youtu.be/dQw4w9WgXcQ") == "youtube"
    assert sources.describe(str(csv)) == "csv"
    assert sources.describe("some song") == "search"
    with pytest.raises(ValueError, match="Unsupported link"):
        sources.describe("https://soundcloud.com/x/y")


def test_resolve_many_concatenates_and_renumbers(monkeypatch, tmp_path):
    def fake_spotify(url, progress=None):
        return Collection("playlist", "PL", "cover", [Track("a", "x", position=1, playlist="PL"),
                                                       Track("b", "y", position=2, playlist="PL")])

    monkeypatch.setattr(public_source, "fetch_collection", fake_spotify)
    monkeypatch.setattr(ytmusic_source, "search", lambda q: Collection("search", q, "", [Track(q, "", position=1)]))
    csv = tmp_path / "Road Trip.csv"
    csv.write_text("Title,Artist\nc,z\n")
    seen = []
    coll = sources.resolve_many([SP, "free text", str(csv)], on_source=lambda i, n, item: seen.append(i))
    assert [t.position for t in coll.tracks] == [1, 2, 3, 4]
    assert [t.playlist for t in coll.tracks] == ["PL", "PL", "", "Road Trip"]
    assert coll.kind == "multiple" and coll.name == "PL + free text + Road Trip" and coll.cover_url == "cover"
    assert seen == [0, 1, 2]


def test_resolve_many_requires_input():
    with pytest.raises(ValueError):
        sources.resolve_many([])


def test_csv_source_still_importable():
    assert callable(csv_source.fetch_tracks)
