#!/usr/bin/env bash
# Kept for old instructions -- the build now lives in packaging/.
exec "$(dirname "$0")/../packaging/build_macos.sh" "$@"
