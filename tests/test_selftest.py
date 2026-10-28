import shutil

import pytest

from melograb import selftest


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_offline_self_test_reports_each_check(monkeypatch):
    from melograb import env

    monkeypatch.setattr(env, "available_js_runtimes", lambda: ["ffmpeg"])  # any runnable tool will do
    lines = []
    ok = selftest.run(emit=lines.append)
    text = "\n".join(lines)
    assert ok, text
    for name in ("yt-dlp", "challenge solver", "ytmusicapi", "ffmpeg", "convert + tag"):
        assert name in text
    assert lines[-1] == "All good."


def test_failures_are_counted(monkeypatch):
    from melograb import env

    monkeypatch.setattr(env, "available_js_runtimes", lambda: [])
    monkeypatch.setattr(selftest, "_convert_and_tag", lambda tmp: (_ for _ in ()).throw(RuntimeError("boom")))
    lines = []
    assert not selftest.run(emit=lines.append)
    assert any(line.startswith("[FAIL] JavaScript runtime") for line in lines)
    assert any("boom" in line for line in lines)
