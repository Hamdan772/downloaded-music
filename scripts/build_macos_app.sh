#!/usr/bin/env bash
# Builds melograb.app -- a standalone, double-clickable macOS app.
# Requires a venv with requirements.txt + pyinstaller installed:
#   pip install -r requirements.txt pyinstaller
set -euo pipefail
cd "$(dirname "$0")/.."

pyinstaller --windowed --noconfirm --name melograb \
  --icon assets/melograb.icns \
  --add-data "assets:assets" \
  --collect-all ytmusicapi \
  --collect-all yt_dlp \
  run_gui.py

echo
echo "Built: dist/melograb.app"
echo "ffmpeg is NOT bundled -- make sure it's installed (brew install ffmpeg)."
