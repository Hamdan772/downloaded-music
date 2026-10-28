#!/usr/bin/env bash
# Downloads the external programs melograb.app bundles -- static ffmpeg +
# ffprobe and deno -- for this Mac's CPU into DEST (default build/tools).
# Each one is test-run after download; anything that fails is left out with
# a warning (the app then falls back to Homebrew copies, if installed).
#
# Overrides: FFMPEG_BASE_URL (folder containing ffmpeg.zip/ffprobe.zip),
#            DENO_VERSION (e.g. v2.5.6; default: latest release).
set -euo pipefail

DEST="${1:-build/tools}"
mkdir -p "$DEST"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

case "$(uname -m)" in
  arm64)  FF_ARCH=arm64; DENO_TARGET=aarch64-apple-darwin ;;
  x86_64) FF_ARCH=amd64; DENO_TARGET=x86_64-apple-darwin ;;
  *) echo "unsupported architecture: $(uname -m)" >&2; exit 1 ;;
esac

warn() { echo "::warning::$*"; echo "warning: $*" >&2; }

fetch_zip() {  # url name -> extracts and installs binary "name" into DEST
  local url="$1" name="$2"
  echo "Fetching $name from $url"
  if ! curl -fsSL --retry 3 --retry-delay 5 -o "$TMP/$name.zip" "$url"; then
    warn "couldn't download $name from $url"; return 1
  fi
  rm -rf "$TMP/$name"
  unzip -q -o "$TMP/$name.zip" -d "$TMP/$name"
  local bin
  bin="$(find "$TMP/$name" -type f -name "$name" | head -n 1)"
  if [ -z "$bin" ]; then warn "$name.zip had no $name binary"; return 1; fi
  install -m 0755 "$bin" "$DEST/$name"
  xattr -c "$DEST/$name" 2>/dev/null || true
}

verify() {  # name flag
  if ! "$DEST/$1" "$2" >/dev/null 2>&1; then
    warn "$1 doesn't run on this machine -- not bundling it"; rm -f "$DEST/$1"; return 1
  fi
  echo "  $("$DEST/$1" "$2" 2>&1 | head -n 1)"
}

# FFmpeg: static, signed macOS builds (https://ffmpeg.martin-riedl.de).
FF_BASE="${FFMPEG_BASE_URL:-https://ffmpeg.martin-riedl.de/redirect/latest/macos/$FF_ARCH/release}"
for tool in ffmpeg ffprobe; do
  fetch_zip "$FF_BASE/$tool.zip" "$tool" && verify "$tool" -version || true
done

# Deno: the JavaScript runtime yt-dlp uses for YouTube's player challenges.
if [ "${DENO_VERSION:-latest}" = "latest" ]; then
  DENO_URL="https://github.com/denoland/deno/releases/latest/download/deno-$DENO_TARGET.zip"
else
  DENO_URL="https://github.com/denoland/deno/releases/download/$DENO_VERSION/deno-$DENO_TARGET.zip"
fi
fetch_zip "$DENO_URL" deno && verify deno --version || true

echo "Bundled tools in $DEST:"
ls -la "$DEST"
