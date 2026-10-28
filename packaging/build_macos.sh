#!/usr/bin/env bash
# Builds melograb.app and wraps it in a drag-to-install disk image:
#   dist/melograb-<version>-macos-<arch>.dmg
#
# Run on a Mac (the GitHub "macOS app" workflow does exactly this). Needs a
# Python 3.11+ with Tk 8.6 -- python.org or Homebrew python + python-tk.
#
# Optional environment:
#   PYTHON                   python to use (default: python3)
#   MELOGRAB_BUNDLE_TOOLS=0  don't bundle ffmpeg/ffprobe/deno
#   MELOGRAB_SIGN_IDENTITY   "Developer ID Application: ..." to sign with
#                            (default: ad-hoc signature)
#   APPLE_ID, APPLE_TEAM_ID, APPLE_APP_PASSWORD
#                            notarize + staple the DMG (needs a signing identity)
set -euo pipefail
cd "$(dirname "$0")/.."

PY="${PYTHON:-python3}"
ARCH="$(uname -m)"
VERSION="$("$PY" -c 'import melograb; print(melograb.__version__)')"
APP="dist/melograb.app"
DMG="dist/melograb-$VERSION-macos-$ARCH.dmg"
IDENTITY="${MELOGRAB_SIGN_IDENTITY:-}"

step() { printf '\n==> %s\n' "$*"; }

step "Installing build dependencies"
"$PY" -m pip install --quiet --upgrade pip
"$PY" -m pip install --quiet -r requirements.txt pyinstaller dmgbuild
"$PY" -c 'import tkinter; assert tkinter.TkVersion >= 8.6, tkinter.TkVersion' \
  || { echo "This Python's Tk is too old for the GUI (need 8.6+)." >&2; exit 1; }

rm -rf build/tools build/pyinstaller build/dmg "$APP" dist/melograb
if [ "${MELOGRAB_BUNDLE_TOOLS:-1}" = "1" ]; then
  step "Fetching ffmpeg, ffprobe and deno"
  packaging/fetch_tools.sh build/tools
fi

step "Building $APP"
MELOGRAB_TOOLS_DIR="$PWD/build/tools" "$PY" -m PyInstaller --noconfirm --clean \
  --distpath dist --workpath build/pyinstaller packaging/melograb.spec

step "Signing"
if [ -n "$IDENTITY" ]; then
  # Inside-out with the hardened runtime, as notarization requires.
  find "$APP/Contents" -type f \( -perm -u+x -o -name '*.dylib' -o -name '*.so' \) -print0 \
    | xargs -0 codesign --force --timestamp --options runtime \
        --entitlements packaging/entitlements.plist --sign "$IDENTITY"
  codesign --force --timestamp --options runtime \
    --entitlements packaging/entitlements.plist --sign "$IDENTITY" "$APP"
else
  codesign --force --deep --sign - "$APP"
fi
codesign --verify --deep --strict "$APP"

step "Self-test of the finished app"
"$APP/Contents/MacOS/melograb" --self-test

step "Creating $DMG"
"$PY" packaging/make_dmg_background.py build/dmg $([ -z "$IDENTITY" ] && echo --unsigned)
rm -f "$DMG"
"$PY" -m dmgbuild -s packaging/dmg_settings.py \
  -D app="$APP" -D background=build/dmg/background.png -D icon=assets/melograb.icns \
  "melograb $VERSION" "$DMG"

if [ -n "$IDENTITY" ]; then
  codesign --force --timestamp --sign "$IDENTITY" "$DMG"
  if [ -n "${APPLE_ID:-}" ] && [ -n "${APPLE_TEAM_ID:-}" ] && [ -n "${APPLE_APP_PASSWORD:-}" ]; then
    step "Notarizing"
    xcrun notarytool submit "$DMG" --apple-id "$APPLE_ID" --team-id "$APPLE_TEAM_ID" \
      --password "$APPLE_APP_PASSWORD" --wait
    xcrun stapler staple "$DMG"
  fi
fi

hdiutil verify "$DMG"
step "Done"
ls -lh "$DMG"
