#!/usr/bin/env bash
# Builds melograb.app -- a standalone, double-clickable macOS app.
# Requires a venv with requirements.txt + pyinstaller installed:
#   pip install -r requirements.txt pyinstaller
set -euo pipefail
cd "$(dirname "$0")/.."

if ! python -c "import yt_dlp_ejs" 2>/dev/null; then
  echo "yt-dlp-ejs is missing -- run: pip install -r requirements.txt" >&2
  exit 1
fi

pyinstaller --windowed --noconfirm --name melograb \
  --icon assets/melograb.icns \
  --add-data "assets:assets" \
  --collect-all ytmusicapi \
  --collect-all yt_dlp \
  --collect-all yt_dlp_ejs \
  run_gui.py

echo
echo "Built: dist/melograb.app"
echo "ffmpeg and deno are NOT bundled -- install them with: brew install ffmpeg deno"
echo "(the app finds Homebrew tools even when launched from Finder)."
