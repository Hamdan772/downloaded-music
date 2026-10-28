from __future__ import annotations

import argparse
import shutil
import sys

from . import csv_source, pipeline, public_source


def main():
    parser = argparse.ArgumentParser(
        prog="melograb",
        description=(
            "Grab a Spotify playlist/album (via its public page, no login or API "
            "key needed, or a CSV export) and build a locally-matched, tagged "
            "MP3/M4A/Opus library from YouTube Music."
        ),
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("url", nargs="?", help="Spotify playlist/album URL (must be public)")
    source.add_argument("--csv", help="CSV file to read tracks from instead of a link")

    parser.add_argument("-o", "--output", default="./downloads", help="Output directory")
    parser.add_argument("-f", "--format", choices=["mp3", "m4a", "opus"], default="mp3")
    parser.add_argument("-w", "--workers", type=int, default=4, help="Concurrent downloads")
    parser.add_argument(
        "--no-album-folders", action="store_true", help="Don't group output into per-album subfolders"
    )
    parser.add_argument("--no-lyrics", action="store_true", help="Don't fetch synced lyrics")
    args = parser.parse_args()

    if shutil.which("ffmpeg") is None:
        print("error: ffmpeg not found on PATH. Install it first (e.g. `brew install ffmpeg`).", file=sys.stderr)
        sys.exit(1)

    if args.csv:
        tracks = csv_source.fetch_tracks(args.csv)
    else:
        name, tracks = public_source.fetch_playlist(args.url)
        print(f"Playlist: {name}")

    print(f"Found {len(tracks)} track(s). Matching + downloading with {args.workers} workers...")
    ok, failed = pipeline.run(
        tracks,
        args.output,
        args.format,
        args.workers,
        group_by_album=not args.no_album_folders,
        fetch_lyrics=not args.no_lyrics,
    )

    print(f"\nDone: {len(ok)} succeeded, {len(failed)} failed.")
    for track, _, err in failed:
        print(f"  FAILED  {track.artist} - {track.title}: {err}")


if __name__ == "__main__":
    main()
