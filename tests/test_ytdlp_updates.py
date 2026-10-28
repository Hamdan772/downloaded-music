import hashlib
import json
import os
import subprocess
import sys
import zipfile

from melograb import ytdlp_updates as u


def make_wheel(path, package, version):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(f"{package}/__init__.py", f"__version__ = {version!r}\n")
        z.writestr(f"{package}/version.py", f"__version__ = {version!r}\n")
        z.writestr(f"{package}/sub/__init__.py", "VALUE = 'from-wheel'\n")
    return path


def test_version_key():
    assert u.version_key("2026.10.26.1") > u.version_key("2026.10.26") > u.version_key("2026.9.30")
    assert u.version_key("") == (0,)


def test_activate_requires_newer_and_frozen(tmp_path, monkeypatch):
    make_wheel(tmp_path / "fake_ytdlp.whl", "yt_dlp", "2099.1.1")
    manifest = {"version": "2099.01.01", "wheels": {"yt-dlp": "fake_ytdlp.whl"}}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    assert u.activate(str(tmp_path)) == ""  # not frozen
    monkeypatch.setattr(u, "bundled_version", lambda: "2100.1.1")
    monkeypatch.delitem(sys.modules, "yt_dlp", raising=False)
    assert u.activate(str(tmp_path), force=True) == ""  # bundled is newer


def test_activate_imports_from_wheel_in_fresh_process(tmp_path):
    make_wheel(tmp_path / "yt_dlp-2099.1.1-py3-none-any.whl", "yt_dlp", "2099.1.1")
    (tmp_path / "manifest.json").write_text(json.dumps(
        {"version": "2099.1.1", "wheels": {"yt-dlp": "yt_dlp-2099.1.1-py3-none-any.whl"}}))
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]);"
        "from melograb import ytdlp_updates as u;"
        "print(u.activate(sys.argv[2], force=True));"
        "import yt_dlp, yt_dlp.sub; print(yt_dlp.__version__, yt_dlp.sub.VALUE, '.whl' in yt_dlp.__file__)"
    )
    out = subprocess.run([sys.executable, "-c", code, root, str(tmp_path)], capture_output=True, text=True, check=True)
    assert out.stdout.split() == ["2099.1.1", "2099.1.1", "from-wheel", "True"]


def test_broken_manifest_is_ignored(tmp_path):
    (tmp_path / "manifest.json").write_text(json.dumps({"version": "2099.1.1", "wheels": {"yt-dlp": "missing.whl"}}))
    assert u.read_manifest(str(tmp_path)) == {}
    (tmp_path / "manifest.json").write_text("not json")
    assert u.read_manifest(str(tmp_path)) == {}


class FakeResp:
    def __init__(self, data=None, content=b""):
        self._data, self.content = data, content

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


def test_install_latest(tmp_path, monkeypatch):
    import requests

    ytdlp_bytes, ejs_bytes = b"yt-dlp wheel", b"ejs wheel"

    def file_entry(name, data):
        return {"packagetype": "bdist_wheel", "filename": name, "url": f"https://files/{name}",
                "digests": {"sha256": hashlib.sha256(data).hexdigest()}}

    responses = {
        "https://pypi.org/pypi/yt-dlp/json": FakeResp({
            "info": {"name": "yt-dlp", "version": "2099.1.1",
                     "requires_dist": ['yt-dlp-ejs==9.9.9; extra == "default"']},
            "urls": [file_entry("yt_dlp-2099.1.1-py3-none-any.whl", ytdlp_bytes)]}),
        "https://pypi.org/pypi/yt-dlp-ejs/9.9.9/json": FakeResp({
            "info": {"name": "yt-dlp-ejs"}, "urls": [file_entry("yt_dlp_ejs-9.9.9-py3-none-any.whl", ejs_bytes)]}),
        "https://files/yt_dlp-2099.1.1-py3-none-any.whl": FakeResp(content=ytdlp_bytes),
        "https://files/yt_dlp_ejs-9.9.9-py3-none-any.whl": FakeResp(content=ejs_bytes),
    }
    monkeypatch.setattr(requests, "get", lambda url, timeout=None: responses[url])
    monkeypatch.setattr(u, "bundled_version", lambda: "2026.1.1")
    (tmp_path / "yt_dlp-2000.1.1-py3-none-any.whl").write_bytes(b"old")

    ok, msg = u.install_latest(str(tmp_path))
    assert ok and "2099.1.1" in msg
    manifest = u.read_manifest(str(tmp_path))
    assert manifest["version"] == "2099.1.1"
    assert sorted(os.listdir(tmp_path)) == ["manifest.json", "yt_dlp-2099.1.1-py3-none-any.whl",
                                            "yt_dlp_ejs-9.9.9-py3-none-any.whl"]
    ok, msg = u.install_latest(str(tmp_path))
    assert ok and "already up to date" in msg


def test_install_rejects_bad_checksum(tmp_path, monkeypatch):
    import requests

    def get(url, timeout=None):
        if url.endswith("/yt-dlp/json"):
            return FakeResp({"info": {"version": "2099.1.1"}, "urls": [
                {"packagetype": "bdist_wheel", "filename": "yt_dlp-2099.1.1-py3-none-any.whl",
                 "url": "https://files/x", "digests": {"sha256": "0" * 64}}]})
        if url.endswith("/yt-dlp-ejs/json"):
            return FakeResp({"info": {}, "urls": [
                {"packagetype": "bdist_wheel", "filename": "yt_dlp_ejs-1-py3-none-any.whl", "url": "https://files/y"}]})
        return FakeResp(content=b"tampered")

    monkeypatch.setattr(requests, "get", get)
    monkeypatch.setattr(u, "bundled_version", lambda: "2026.1.1")
    ok, msg = u.install_latest(str(tmp_path))
    assert not ok and "checksum" in msg
    assert u.read_manifest(str(tmp_path)) == {}
