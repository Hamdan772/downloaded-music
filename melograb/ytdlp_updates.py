"""Keep yt-dlp current inside the packaged app.

YouTube breaks old yt-dlp releases within weeks, but a bundled .app can't
`pip install`. Instead, "Update yt-dlp" downloads the newest yt-dlp wheel
(plus the yt-dlp-ejs solver version it pins) from PyPI into the user's
Application Support folder, and on the next launch `activate()` makes
Python import yt-dlp from those wheels instead of the bundled copy. Wheels
are plain zip files, which Python imports directly (the official yt-dlp
executable is itself a zipped Python app).

This module must not import yt_dlp: `activate()` has to run before anything
else does.
"""
from __future__ import annotations

import hashlib
import importlib.machinery
import json
import logging
import os
import re
import sys
from typing import Optional

log = logging.getLogger(__name__)

MANIFEST = "manifest.json"
_PACKAGES = {"yt-dlp": "yt_dlp", "yt-dlp-ejs": "yt_dlp_ejs"}


def updates_dir() -> str:
    from . import env

    return os.path.join(env.config_dir(), "yt-dlp")


def version_key(version: str) -> tuple:
    """'2026.10.26' / '2026.10.26.1' -> comparable tuple; junk sorts lowest."""
    parts = re.findall(r"\d+", version or "")
    return tuple(int(p) for p in parts) if parts else (0,)


def bundled_version() -> str:
    """The yt-dlp version shipped with the app, read from package metadata
    so yt_dlp itself isn't imported."""
    try:
        from importlib.metadata import version

        return version("yt-dlp")
    except Exception:  # noqa: BLE001 - metadata missing in some bundles
        return ""


def read_manifest(directory: Optional[str] = None) -> dict:
    directory = directory or updates_dir()
    try:
        with open(os.path.join(directory, MANIFEST), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or not isinstance(data.get("wheels"), dict):
        return {}
    wheels = {name: os.path.join(directory, fn) for name, fn in data["wheels"].items()}
    if not all(os.path.isfile(p) for p in wheels.values()):
        return {}
    return {"version": str(data.get("version", "")), "wheels": wheels}


class _WheelFinder:
    """Meta-path finder that serves the given top-level packages from wheel
    files. Sits in front of PyInstaller's frozen importer, which a plain
    sys.path entry can't override."""

    def __init__(self, wheels: dict):
        self.wheels = wheels  # import name -> wheel path

    def find_spec(self, fullname, path=None, target=None):
        top = fullname.partition(".")[0]
        wheel = self.wheels.get(top)
        if wheel is None:
            return None
        search = [wheel] if "." not in fullname else path
        return importlib.machinery.PathFinder.find_spec(fullname, search, target)


def activate(directory: Optional[str] = None, force: bool = False) -> str:
    """Use downloaded wheels if they're newer than the bundled yt-dlp (only
    in the packaged app unless `force`). Returns the active override version
    or ''."""
    if not (force or getattr(sys, "frozen", False)):
        return ""
    if "yt_dlp" in sys.modules:
        log.warning("yt_dlp already imported; can't switch to the updated copy")
        return ""
    manifest = read_manifest(directory)
    if not manifest or version_key(manifest["version"]) <= version_key(bundled_version()):
        return ""
    wheels = {_PACKAGES[name]: path for name, path in manifest["wheels"].items() if name in _PACKAGES}
    sys.meta_path.insert(0, _WheelFinder(wheels))
    log.info("using updated yt-dlp %s", manifest["version"])
    return manifest["version"]


def active_version() -> str:
    """Version of the yt-dlp that's actually loaded (or would be)."""
    try:
        from yt_dlp.version import __version__
    except ImportError:
        return ""
    return __version__


# ---------- downloading an update ----------


def _pypi(path: str, timeout: int) -> dict:
    import requests

    resp = requests.get(f"https://pypi.org/pypi/{path}/json", timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _pick_wheel(release: dict) -> dict:
    for f in release.get("urls") or []:
        if f.get("packagetype") == "bdist_wheel" and f["filename"].endswith("-py3-none-any.whl"):
            return f
    raise ValueError(f"no pure-Python wheel for {release.get('info', {}).get('name')}")


def _ejs_requirement(info: dict) -> str:
    """The exact yt-dlp-ejs version a yt-dlp release pins, or '' for latest."""
    for req in info.get("requires_dist") or []:
        m = re.match(r"yt-dlp-ejs\s*==\s*([\w.]+)", req)
        if m:
            return m.group(1)
    return ""


def _download(file_info: dict, directory: str, timeout: int) -> str:
    import requests

    resp = requests.get(file_info["url"], timeout=timeout)
    resp.raise_for_status()
    expected = (file_info.get("digests") or {}).get("sha256")
    if expected and hashlib.sha256(resp.content).hexdigest() != expected:
        raise ValueError(f"checksum mismatch for {file_info['filename']}")
    path = os.path.join(directory, file_info["filename"])
    with open(path + ".part", "wb") as f:
        f.write(resp.content)
    os.replace(path + ".part", path)
    return path


def install_latest(directory: Optional[str] = None, timeout: int = 60) -> tuple[bool, str]:
    """Download the newest yt-dlp (+ matching yt-dlp-ejs) wheels. Takes effect
    on next launch. Returns (ok, message)."""
    directory = directory or updates_dir()
    try:
        release = _pypi("yt-dlp", timeout)
        version = release["info"]["version"]
        current = max(version_key(bundled_version()), version_key(read_manifest(directory).get("version", "")))
        if version_key(version) <= current:
            return True, f"yt-dlp is already up to date ({version})."
        ejs_version = _ejs_requirement(release["info"])
        ejs = _pypi(f"yt-dlp-ejs/{ejs_version}" if ejs_version else "yt-dlp-ejs", timeout)

        os.makedirs(directory, exist_ok=True)
        wheels = {
            "yt-dlp": os.path.basename(_download(_pick_wheel(release), directory, timeout)),
            "yt-dlp-ejs": os.path.basename(_download(_pick_wheel(ejs), directory, timeout)),
        }
        tmp = os.path.join(directory, MANIFEST + ".part")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"version": version, "wheels": wheels}, f, indent=2)
        os.replace(tmp, os.path.join(directory, MANIFEST))
        for name in os.listdir(directory):  # drop superseded wheels
            if name.endswith(".whl") and name not in wheels.values():
                try:
                    os.remove(os.path.join(directory, name))
                except OSError:
                    pass
        return True, f"Downloaded yt-dlp {version}. Restart melograb to use it."
    except Exception as e:  # noqa: BLE001
        log.exception("yt-dlp update failed")
        return False, f"Couldn't update yt-dlp: {e}"
