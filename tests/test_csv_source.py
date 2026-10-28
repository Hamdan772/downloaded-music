import pytest

from melograb import csv_source


def test_exportify_columns(tmp_path):
    p = tmp_path / "list.csv"
    p.write_text(
        "Track URI,Track Name,Artist Name(s),Album Name,Album Artist Name(s),Album Release Date,"
        "Track Number,Duration (ms),ISRC\n"
        'spotify:track:abc,Mosquito,"PinkPantheress",Fancy That,PinkPantheress,2025-05-09,3,150000,GBAHS2500001\n',
        encoding="utf-8",
    )
    (t,) = csv_source.fetch_tracks(str(p))
    assert (t.title, t.artist, t.album, t.track_number, t.duration_ms) == (
        "Mosquito", "PinkPantheress", "Fancy That", 3, 150000,
    )
    assert (t.isrc, t.release_date, t.spotify_id, t.position) == ("GBAHS2500001", "2025-05-09", "abc", 1)


def test_tunemymusic_style_and_bom(tmp_path):
    p = tmp_path / "list.csv"
    p.write_text("﻿Title,Artist,Album,Duration\nSong A,Artist A,,3:25\n,Skipped,,\nSong B,Artist B,X,\n",
                 encoding="utf-8")
    a, b = csv_source.fetch_tracks(str(p))
    assert (a.title, a.duration_ms, a.track_number, a.position) == ("Song A", 205_000, 0, 1)
    assert (b.album, b.position) == ("X", 2)


def test_semicolon_delimited(tmp_path):
    p = tmp_path / "list.csv"
    p.write_text("Title;Artist\nSong;Someone\n", encoding="utf-8")
    (t,) = csv_source.fetch_tracks(str(p))
    assert (t.title, t.artist) == ("Song", "Someone")


def test_no_rows_raises(tmp_path):
    p = tmp_path / "list.csv"
    p.write_text("foo,bar\n1,2\n", encoding="utf-8")
    with pytest.raises(ValueError):
        csv_source.fetch_tracks(str(p))
