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


def test_latest_app_release(monkeypatch):
    import requests

    class Resp:
        status_code = 200

        def json(self):
            return {"tag_name": "v0.9.0", "html_url": "https://github.com/x/y/releases/tag/v0.9.0"}

    monkeypatch.setattr(requests, "get", lambda *a, **k: Resp())
    assert env.latest_app_release("x/y") == {"version": "0.9.0", "url": "https://github.com/x/y/releases/tag/v0.9.0"}

    class Missing:
        status_code = 404

    monkeypatch.setattr(requests, "get", lambda *a, **k: Missing())
    assert env.latest_app_release("x/y") == {}


def test_bundled_bin_dir_goes_first(monkeypatch, tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    tool = bin_dir / "ffmpeg"
    tool.write_text("#!/bin/sh\n")
    tool.chmod(0o644)
    monkeypatch.setattr(env.sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setattr(env, "_EXTRA_BIN_DIRS", ())
    monkeypatch.setenv("PATH", "/usr/bin")
    env.ensure_tool_paths()
    assert os.environ["PATH"].split(os.pathsep)[0] == str(bin_dir)
    assert os.access(tool, os.X_OK)  # made executable


def test_browser_from_launch_services():
    data = {"LSHandlers": [
        {"LSHandlerContentType": "public.html", "LSHandlerRoleAll": "com.apple.safari"},
        {"LSHandlerURLScheme": "https", "LSHandlerRoleAll": "com.google.Chrome"},
    ]}
    assert env.browser_from_launch_services(data) == "chrome"
    assert env.browser_from_launch_services({"LSHandlers": [
        {"LSHandlerURLScheme": "https", "LSHandlerRoleAll": "com.example.unknown"}]}) == ""
    assert env.browser_from_launch_services({}) == ""


def test_default_browser_reads_plist(monkeypatch, tmp_path):
    import plistlib

    prefs = tmp_path / "Library/Preferences/com.apple.LaunchServices"
    prefs.mkdir(parents=True)
    with open(prefs / "com.apple.launchservices.secure.plist", "wb") as f:
        plistlib.dump({"LSHandlers": [{"LSHandlerURLScheme": "https",
                                       "LSHandlerRoleAll": "org.mozilla.firefox"}]}, f, fmt=plistlib.FMT_BINARY)
    monkeypatch.setattr(env.sys, "platform", "darwin")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert env.default_browser() == "firefox"
    (prefs / "com.apple.launchservices.secure.plist").unlink()
    assert env.default_browser() == "safari"  # never changed the default
