"""Runtime environment helpers: locating external tools, logging, and the
per-user config/log directories.

Apps launched from Finder/Dock on macOS get a minimal PATH that doesn't
include Homebrew, so ffmpeg and deno look "missing" even when installed.
`ensure_tool_paths()` fixes that before anything shells out.
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import shutil
import subprocess
import sys
from datetime import date

_EXTRA_BIN_DIRS = (
    "/opt/homebrew/bin",  # Homebrew on Apple Silicon
    "/usr/local/bin",  # Homebrew on Intel, most manual installs
    "/opt/local/bin",  # MacPorts
    os.path.expanduser("~/.deno/bin"),  # deno's official installer
    os.path.expanduser("~/.bun/bin"),
)

# JavaScript runtimes yt-dlp can use to solve YouTube's player challenges,
# in yt-dlp's own order of preference.
JS_RUNTIMES = ("deno", "node", "bun", "quickjs")


def ensure_tool_paths() -> None:
    path = os.environ.get("PATH", "")
    parts = path.split(os.pathsep) if path else []
    extra = [d for d in _EXTRA_BIN_DIRS if os.path.isdir(d) and d not in parts]
    if extra:
        os.environ["PATH"] = os.pathsep.join(parts + extra)


def has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def available_js_runtimes() -> list[str]:
    return [name for name in JS_RUNTIMES if shutil.which(name)]


def has_ejs() -> bool:
    try:
        import yt_dlp_ejs  # noqa: F401
    except ImportError:
        return False
    return True


def environment_warnings() -> list[str]:
    """Human-readable problems that will make downloads fail or degrade."""
    warnings = []
    if not has_ffmpeg():
        warnings.append(
            "ffmpeg/ffprobe not found -- audio can't be converted. Install it (macOS: `brew install ffmpeg`)."
        )
    if not available_js_runtimes():
        warnings.append(
            "No JavaScript runtime found -- YouTube downloads may fail or be lower quality. "
            "Install deno (macOS: `brew install deno`)."
        )
    elif not has_ejs():
        warnings.append(
            "yt-dlp's challenge solver is missing -- run `pip install -U \"yt-dlp[default]\"`."
        )
    return warnings


def _user_dir(kind: str) -> str:
    home = os.path.expanduser("~")
    if sys.platform == "darwin":
        base = os.path.join(home, "Library", "Logs" if kind == "logs" else "Application Support")
    elif sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.join(home, "AppData", "Local")
    else:
        default = os.path.join(home, ".cache" if kind == "logs" else ".config")
        base = os.environ.get("XDG_CACHE_HOME" if kind == "logs" else "XDG_CONFIG_HOME") or default
    return os.path.join(base, "melograb")


def config_dir() -> str:
    return _user_dir("config")


def log_path() -> str:
    return os.path.join(_user_dir("logs"), "melograb.log")


def setup_logging(level: int = logging.INFO) -> str:
    """Log to a file (always) so failures can be diagnosed after the fact.
    Returns the log file path."""
    path = log_path()
    root = logging.getLogger("melograb")
    if root.handlers:
        return path
    root.setLevel(level)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            path, maxBytes=2_000_000, backupCount=2, encoding="utf-8"
        )
    except OSError:
        handler = logging.NullHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(threadName)s] %(name)s: %(message)s"))
    root.addHandler(handler)
    return path


# ---------- yt-dlp freshness ----------
# YouTube changes constantly and yt-dlp ships fixes within days, so an old
# yt-dlp is the most common reason *every* download suddenly fails.

STALE_AFTER_DAYS = 30


def ytdlp_version() -> str:
    try:
        from yt_dlp.version import __version__
    except ImportError:
        return ""
    return __version__


def _version_date(version: str):
    try:
        y, m, d = (int(p) for p in version.split(".")[:3])
        return date(y, m, d)
    except (ValueError, TypeError):
        return None


def latest_ytdlp_version(timeout: int = 6) -> str:
    import requests

    try:
        resp = requests.get("https://pypi.org/pypi/yt-dlp/json", timeout=timeout)
        resp.raise_for_status()
        return resp.json()["info"]["version"]
    except Exception:  # noqa: BLE001 - offline etc.: just don't warn
        return ""


def ytdlp_update_warning(latest: str | None = None) -> str:
    """A warning if the installed yt-dlp is well behind the latest release,
    else ''. Pass `latest` to skip the network lookup."""
    installed = ytdlp_version()
    latest = latest if latest is not None else latest_ytdlp_version()
    have, newest = _version_date(installed), _version_date(latest)
    if not have or not newest or (newest - have).days < STALE_AFTER_DAYS:
        return ""
    return (
        f"yt-dlp {installed} is out of date (latest: {latest}) -- YouTube downloads may fail. "
        'Update with `pip install -U "yt-dlp[default]"`.'
    )


def can_self_update() -> bool:
    # A PyInstaller bundle can't pip-install into itself.
    return not getattr(sys, "frozen", False)


def update_ytdlp(timeout: int = 300) -> tuple[bool, str]:
    """pip-install the latest yt-dlp into this Python. Takes effect on restart."""
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-U", "yt-dlp[default]"],
            capture_output=True, text=True, timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)
    output = (proc.stdout + proc.stderr).strip()
    return proc.returncode == 0, output[-2000:]
