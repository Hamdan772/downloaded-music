import os

import pytest
from conftest import make_audio, requires_ffmpeg

from melograb import library, lyrics, tag
from melograb.track import Track


@pytest.fixture
def tagged_folder(tmp_path):
    os.makedirs(tmp_path / "Album")
    a = make_audio(str(tmp_path / "Album" / "a.mp3"))
    tag.tag_file(a, Track("Husk", "Men I Trust"))
    b = make_audio(str(tmp_path / "b.m4a"))
    tag.tag_file(b, Track("Missing", "Nobody"))
    make_audio(str(tmp_path / "untagged.opus"))
    os.makedirs(tmp_path / ".hidden")
    make_audio(str(tmp_path / ".hidden" / "skip.mp3"))
    return tmp_path


@requires_ffmpeg
def test_add_lyrics_to_folder(tagged_folder, monkeypatch):
    def fake_fetch(track):
        assert track.duration_ms > 0
        return ("[00:01.00]hi", "hi") if track.title == "Husk" else ("", "")

    monkeypatch.setattr(lyrics, "fetch_lyrics", fake_fetch)
    results = library.add_lyrics_to_folder(str(tagged_folder))
    rel = {os.path.relpath(p, tagged_folder): o for p, o in results.items()}
    assert rel == {os.path.join("Album", "a.mp3"): "synced", "b.m4a": "not found", "untagged.opus": "no tags"}
    # Second pass leaves existing lyrics alone unless asked.
    again = library.add_lyrics_to_folder(str(tagged_folder))
    assert again[str(tagged_folder / "Album" / "a.mp3")] == "has lyrics"
    forced = library.add_lyrics_to_folder(str(tagged_folder), overwrite=True)
    assert forced[str(tagged_folder / "Album" / "a.mp3")] == "synced"
