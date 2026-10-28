#!/usr/bin/env bash
# Downloads the external programs melograb.app bundles -- static ffmpeg +
# ffprobe and deno -- for this Mac's CPU into DEST (default build/tools).
# Each one is test-run after download. ffmpeg is required (with a PyPI
# fallback if the primary host is down); ffprobe and deno are left out with
# a warning if they can't be fetched.
#
# Overrides: FFMPEG_BASE_URL (folder containing ffmpeg.zip/ffprobe.zip),
#            DENO_VERSION (e.g. v2.5.6; default: latest release).
set -euo pipefail

DEST="${1:-build/tools}"
mkdir -p "$DEST"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

PY="${PYTHON:-python3}"
case "$(uname -m)" in
  arm64|aarch64) FF_ARCH=arm64; DENO_TARGET=aarch64-apple-darwin ;;
  x86_64)        FF_ARCH=amd64; DENO_TARGET=x86_64-apple-darwin ;;
  *) echo "unsupported architecture: $(uname -m)" >&2; exit 1 ;;
esac

warn() { echo "::warning::$*"; echo "warning: $*" >&2; }

fetch_zip() {  # url name -> extracts and installs binary "name" into DEST
  local url="$1" name="$2"
  echo "Fetching $name from $url"
  # HTTP/1.1 + retry on *any* error: some hosts drop HTTP/2 streams midway
  # (curl error 92), which plain --retry doesn't cover. Time-boxed so a
  # crawling mirror can't stall the build.
  if ! curl -fsSL --http1.1 --retry 5 --retry-all-errors --retry-delay 5 \
       --connect-timeout 20 --max-time 240 -o "$TMP/$name.zip" "$url"; then
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

# Fallback for ffmpeg: the static binary inside the imageio-ffmpeg wheel on
# PyPI (built for this CPU; it has no ffprobe, which is optional).
if [ ! -x "$DEST/ffmpeg" ]; then
  echo "Falling back to ffmpeg from the imageio-ffmpeg wheel on PyPI"
  if "$PY" -m pip download --quiet --only-binary=:all: --no-deps imageio-ffmpeg -d "$TMP/iio"; then
    unzip -q -o "$TMP"/iio/imageio_ffmpeg-*.whl -d "$TMP/iio/x"
    bin="$(find "$TMP/iio/x/imageio_ffmpeg/binaries" -type f -name 'ffmpeg-*' | head -n 1)"
    if [ -n "$bin" ]; then
      install -m 0755 "$bin" "$DEST/ffmpeg"
      verify ffmpeg -version || true
    fi
  fi
fi
if [ ! -x "$DEST/ffmpeg" ]; then
  echo "::error::couldn't get a working ffmpeg from any source" >&2
  exit 1
fi

# Deno: the JavaScript runtime yt-dlp uses for YouTube's player challenges.
if [ "${DENO_VERSION:-latest}" = "latest" ]; then
  DENO_URL="https://github.com/denoland/deno/releases/latest/download/deno-$DENO_TARGET.zip"
else
  DENO_URL="https://github.com/denoland/deno/releases/download/$DENO_VERSION/deno-$DENO_TARGET.zip"
fi
fetch_zip "$DENO_URL" deno && verify deno --version || true

echo "Bundled tools in $DEST:"
ls -la "$DEST"
