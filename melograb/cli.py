from __future__ import annotations

import argparse
import os
import sys
import threading

from tqdm import tqdm

from . import __version__, downloader, env, library, pipeline, selftest, sources
from . import template as templates

_EPILOG = """\
inputs can be mixed and repeated:
  Spotify       playlist / album / track / artist links (incl. spotify.link share links)
  YouTube       youtube.com or music.youtube.com playlist / album / video links
  CSV           path to a .csv export (TuneMyMusic, Exportify, ...)
  search        any other text, e.g. "mac demarco - chamber of reflection"

template fields: {fields}
template presets:
{presets}
"""


def _positive_int(value: str) -> int:
    n = int(value)
    if n < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return n


def _template(value: str) -> str:
    try:
        templates.validate(value)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e)) from e
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="melograb",
        description=(
            "Grab Spotify / YouTube Music playlists, albums and songs (public pages, no login "
            "or API key) or CSV exports, and build a tagged MP3/M4A/Opus library with lyrics."
        ),
        epilog=_EPILOG.format(
            fields=", ".join("{" + f + "}" for f in templates.FIELDS),
            presets="\n".join(f"  {v:<45} {k}" for k, v in templates.PRESETS.items()),
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("inputs", nargs="*", metavar="INPUT", help="links, CSV files or search text (see below)")
    parser.add_argument("--csv", action="append", default=[], help="CSV file to read tracks from (repeatable)")

    out = parser.add_argument_group("output")
    out.add_argument("-o", "--output", default="./downloads", help="output directory (default: ./downloads)")
    out.add_argument("-f", "--format", choices=downloader.FORMATS, default="mp3")
    out.add_argument("-q", "--quality", choices=downloader.QUALITIES, default="best",
                     help="bitrate when converting (default: best)")
    out.add_argument("-t", "--template", type=_template, default=None,
                     help=f"file/folder layout (default: {templates.DEFAULT_TEMPLATE!r})")
    out.add_argument("--no-album-folders", action="store_true",
                     help=f"shorthand for --template {templates.FLAT_TEMPLATE!r}")
    out.add_argument("--no-lyrics", action="store_true", help="don't fetch lyrics")
    out.add_argument("--no-sponsorblock", action="store_true",
                     help="keep non-music intros/outros in music-video matches")
    out.add_argument("--no-playlist-file", action="store_true", help="don't write an .m3u8 playlist")
    out.add_argument("--cookies-from-browser", choices=downloader.BROWSERS, metavar="BROWSER",
                     help="use your YouTube login from this browser when YouTube asks to confirm you're not a bot "
                          f"({', '.join(downloader.BROWSERS)})")

    run = parser.add_argument_group("run")
    run.add_argument("-w", "--workers", type=_positive_int, default=4, help="concurrent downloads (default: 4)")
    run.add_argument("--dry-run", action="store_true", help="show what each track matches, download nothing")

    lib = parser.add_argument_group("existing library")
    lib.add_argument("--add-lyrics", metavar="DIR", help="add lyrics to every song already in DIR, then exit")
    lib.add_argument("--overwrite-lyrics", action="store_true", help="with --add-lyrics: replace existing lyrics")

    diag = parser.add_argument_group("diagnostics")
    diag.add_argument("--self-test", action="store_true", help="check that everything melograb needs works, then exit")
    diag.add_argument("--online", action="store_true", help="with --self-test: also fetch an album and download a song")

    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def _add_lyrics(args) -> None:
    if not os.path.isdir(args.add_lyrics):
        print(f"error: not a folder: {args.add_lyrics}", file=sys.stderr)
        sys.exit(1)
    files = list(library.iter_audio_files(args.add_lyrics))
    print(f"Adding lyrics to {len(files)} file(s) in {args.add_lyrics}...")
    lock = threading.Lock()
    with tqdm(total=len(files), unit="file") as bar:

        def on_result(path, outcome):
            with lock:
                if outcome.startswith("error") or outcome == "no tags":
                    bar.write(f"  {outcome:<10} {os.path.relpath(path, args.add_lyrics)}")
                bar.update(1)

        results = library.add_lyrics_to_folder(
            args.add_lyrics, overwrite=args.overwrite_lyrics, workers=args.workers, on_result=on_result
        )
    counts: dict = {}
    for outcome in results.values():
        key = "error" if outcome.startswith("error") else outcome
        counts[key] = counts.get(key, 0) + 1
    print("Done: " + ", ".join(f"{n} {k}" for k, n in sorted(counts.items(), key=lambda p: -p[1])))


def _dry_run(tracks, workers) -> None:
    print(f"Matching {len(tracks)} track(s) (nothing will be downloaded)...\n")
    matches = pipeline.preview(tracks, workers=workers)
    missing = 0
    for t, m in zip(tracks, matches, strict=True):
        if m is None:
            missing += 1
            print(f"  ✗ {t.display_name}\n      no match")
            continue
        score = f"  [{m['_score']}]" if "_score" in m else ""
        print(f"  ✓ {t.display_name}\n      → {m['display']}{score}  https://music.youtube.com/watch?v={m['videoId']}")
    print(f"\n{len(tracks) - missing} matched, {missing} without a match.")


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    env.ensure_tool_paths()
    log_file = env.setup_logging()

    if args.self_test:
        sys.exit(0 if selftest.run(online=args.online) else 1)
    if args.add_lyrics:
        _add_lyrics(args)
        return

    items = list(args.inputs) + list(args.csv)
    if not items:
        parser.error("give at least one link, CSV file or search (or --add-lyrics DIR)")
    if args.template and args.no_album_folders:
        parser.error("use either --template or --no-album-folders, not both")
    options = pipeline.Options(
        fmt=args.format,
        template=args.template or (templates.FLAT_TEMPLATE if args.no_album_folders else templates.DEFAULT_TEMPLATE),
        fetch_lyrics=not args.no_lyrics,
        quality=args.quality,
        remove_non_music=not args.no_sponsorblock,
        workers=args.workers,
        cookies_browser=args.cookies_from_browser or "",
    )

    if not args.dry_run:
        for w in env.environment_warnings():
            print(f"warning: {w}", file=sys.stderr)
        if not env.has_ffmpeg():
            sys.exit(1)

    try:
        for item in items:
            sources.describe(item)  # fail fast on unsupported links
        print("Fetching tracklist...")
        with tqdm(desc="Looking up albums", unit="track", leave=False, disable=None) as bar:

            def progress(done, total):
                bar.total = total
                bar.n = done
                bar.refresh()

            collection = sources.resolve_many(
                items, progress=progress, on_source=lambda i, n, item: bar.write(f"  [{i + 1}/{n}] {item}")
            )
    except (OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
    tracks = collection.tracks
    print(f"{collection.name} — {len(tracks)} track(s)")

    if args.dry_run:
        _dry_run(tracks, args.workers)
        return

    print(f"Matching + downloading with {args.workers} workers...")
    lock = threading.Lock()
    with tqdm(total=len(tracks), desc="Downloading", unit="track") as bar:

        def on_result(_, result):
            with lock:
                if result.status == "failed":
                    bar.write(f"  failed  {result.track.display_name}: {result.error}")
                bar.update(1)

        try:
            results = pipeline.run(tracks, args.output, options, on_result=on_result)
        except KeyboardInterrupt:
            print("\nCancelled.", file=sys.stderr)
            sys.exit(130)

    done = sum(r.status == "done" for r in results)
    skipped = sum(r.status in ("skipped", "duplicate") for r in results)
    failed = [r for r in results if not r.ok]
    print(f"\nDone: {done} downloaded, {skipped} already present, {len(failed)} failed.")

    if not args.no_playlist_file and len(tracks) > 1:
        m3u = pipeline.write_m3u(results, args.output, collection.name)
        if m3u:
            print(f"Playlist: {m3u}")

    if failed:
        print("\nFailures by reason:")
        for reason, count in pipeline.summarize_failures(results):
            print(f"  {count:>3} x {reason}")
        stale = env.ytdlp_update_warning()
        if stale:
            print(f"\nwarning: {stale}", file=sys.stderr)
        print(f"\nDetails: {log_file}")
        print("Re-run the same command to retry -- finished tracks are skipped.")
        sys.exit(2)


if __name__ == "__main__":
    main()
