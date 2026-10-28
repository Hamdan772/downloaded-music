import pytest

from melograb import cli, env, pipeline, sources
from melograb.track import Collection, Track


@pytest.fixture(autouse=True)
def quiet_env(monkeypatch, tmp_path):
    monkeypatch.setattr(env, "setup_logging", lambda: str(tmp_path / "log"))
    monkeypatch.setattr(env, "environment_warnings", lambda: [])
    monkeypatch.setattr(env, "has_ffmpeg", lambda: True)
    monkeypatch.setattr(env, "ytdlp_update_warning", lambda: "")


@pytest.fixture
def one_track(monkeypatch):
    coll = Collection("search", "A - T", "", [Track("T", "A", position=1)])
    monkeypatch.setattr(sources, "resolve_many", lambda items, progress=None, on_source=None: coll)
    return coll


def test_requires_input(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main([])
    assert e.value.code == 2


def test_bad_template_rejected(capsys):
    with pytest.raises(SystemExit):
        cli.main(["x", "--template", "{nope} {title}"])
    assert "unknown field" in capsys.readouterr().err


def test_unsupported_link(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["https://soundcloud.com/a/b"])
    assert e.value.code == 1 and "Unsupported link" in capsys.readouterr().err


def test_dry_run(one_track, monkeypatch, capsys):
    monkeypatch.setattr(pipeline, "preview", lambda tracks, workers=4: [
        {"videoId": "v", "display": "A - T", "_score": 99.0}])
    monkeypatch.setattr(pipeline, "run", lambda *a, **k: pytest.fail("dry run downloaded"))
    cli.main(["a - t", "--dry-run"])
    out = capsys.readouterr().out
    assert "→ A - T  [99.0]" in out and "1 matched, 0 without a match" in out


def test_download_options_and_exit_code(one_track, monkeypatch, tmp_path, capsys):
    seen = {}

    def fake_run(tracks, out_dir, options, on_result=None, **kw):
        seen["options"] = options
        r = pipeline.Result(tracks[0], error="video is unavailable")
        on_result(0, r)
        return [r]

    monkeypatch.setattr(pipeline, "run", fake_run)
    with pytest.raises(SystemExit) as e:
        cli.main(["a - t", "-o", str(tmp_path), "--no-album-folders", "-q", "192", "--no-sponsorblock"])
    assert e.value.code == 2
    o = seen["options"]
    assert (o.template, o.quality, o.remove_non_music) == ("{artist} - {title}", "192", False)
    assert "1 x video is unavailable" in capsys.readouterr().out


def test_add_lyrics_mode(monkeypatch, tmp_path, capsys):
    from melograb import library

    monkeypatch.setattr(library, "iter_audio_files", lambda root: ["a", "b"])
    monkeypatch.setattr(library, "add_lyrics_to_folder",
                        lambda root, overwrite, workers, on_result: {"a": "synced", "b": "not found"})
    cli.main(["--add-lyrics", str(tmp_path)])
    assert "1 synced, 1 not found" in capsys.readouterr().out
