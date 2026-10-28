from __future__ import annotations

import argparse
import os
import sys
import threading

from tqdm import tqdm

from . import __version__, csv_source, downloader, env, pipeline, public_source


def _positive_int(value: str) -> int:
    n = int(value)
    if n < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return n


def build_parser() -> argparse.ArgumentParser:
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

    parser.add_argument("-o", "--output", default="./downloads", help="Output directory (default: ./downloads)")
    parser.add_argument("-f", "--format", choices=downloader.FORMATS, default="mp3")
    parser.add_argument("-w", "--workers", type=_positive_int, default=4, help="Concurrent downloads (default: 4)")
    parser.add_argument(
        "--no-album-folders", action="store_true", help="Don't group output into per-album subfolders"
    )
    parser.add_argument("--no-lyrics", action="store_true", help="Don't fetch lyrics")
    parser.add_argument("--no-playlist-file", action="store_true", help="Don't write an .m3u8 playlist")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    env.ensure_tool_paths()
    log_file = env.setup_logging()

    warnings = env.environment_warnings()
    for w in warnings:
        print(f"warning: {w}", file=sys.stderr)
    if not env.has_ffmpeg():
        sys.exit(1)

    try:
        if args.csv:
            tracks = csv_source.fetch_tracks(args.csv)
            name = os.path.splitext(os.path.basename(args.csv))[0]
        else:
            public_source.parse_spotify_url(args.url)
            print("Fetching tracklist...")
            with tqdm(desc="Looking up albums", unit="track", leave=False) as bar:

                def progress(done, total):
                    bar.total = total
                    bar.n = done
                    bar.refresh()

                collection = public_source.fetch_collection(args.url, progress=progress)
            name, tracks = collection.name, collection.tracks
            print(f"{collection.kind.title()}: {name}")
    except (OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Found {len(tracks)} track(s). Matching + downloading with {args.workers} workers...")
    lock = threading.Lock()
    with tqdm(total=len(tracks), desc="Downloading", unit="track") as bar:

        def on_result(_, result):
            with lock:
                if result.status == "failed":
                    bar.write(f"  failed  {result.track.display_name}: {result.error}")
                bar.update(1)

        try:
            results = pipeline.run(
                tracks,
                args.output,
                args.format,
                args.workers,
                group_by_album=not args.no_album_folders,
                fetch_lyrics=not args.no_lyrics,
                on_result=on_result,
            )
        except KeyboardInterrupt:
            print("\nCancelled.", file=sys.stderr)
            sys.exit(130)

    done = sum(r.status == "done" for r in results)
    skipped = sum(r.status in ("skipped", "duplicate") for r in results)
    failed = [r for r in results if not r.ok]
    print(f"\nDone: {done} downloaded, {skipped} already present, {len(failed)} failed.")

    if not args.no_playlist_file:
        m3u = pipeline.write_m3u(results, args.output, name)
        if m3u:
            print(f"Playlist: {m3u}")

    if failed:
        print("\nFailures by reason:")
        for reason, count in pipeline.summarize_failures(results):
            print(f"  {count:>3} x {reason}")
        print(f"\nDetails: {log_file}")
        print("Re-run the same command to retry -- finished tracks are skipped.")
        sys.exit(2)


if __name__ == "__main__":
    main()
