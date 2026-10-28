import os

from melograb import env


def test_update_warning(monkeypatch):
    monkeypatch.setattr(env, "ytdlp_version", lambda: "2026.01.01")
    assert "out of date" in env.ytdlp_update_warning(latest="2026.10.26")
    assert env.ytdlp_update_warning(latest="2026.01.20") == ""  # recent enough
    assert env.ytdlp_update_warning(latest="") == ""  # offline


def test_ensure_tool_paths_adds_existing_dirs(monkeypatch, tmp_path):
    monkeypatch.setattr(env, "_EXTRA_BIN_DIRS", (str(tmp_path), "/definitely/not/here"))
    monkeypatch.setenv("PATH", "/usr/bin")
    env.ensure_tool_paths()
    assert os.environ["PATH"] == os.pathsep.join(["/usr/bin", str(tmp_path)])
    env.ensure_tool_paths()  # idempotent
    assert os.environ["PATH"].count(str(tmp_path)) == 1
