# melograb

Takes the useful idea from both `spotdl` and `CSVmusic` and merges them into one tool, no Spotify Premium or API keys required:

- **Metadata source** — reads the same public, no-login page data Spotify serves to any logged-out visitor (playlist/album title, tracklist, durations, cover art), with each playlist track enriched against its own public page for its *real* album, album artist, cover, release date and in-album track number. CSV import (TuneMyMusic, Exportify, or anything with Title/Artist columns) works too.
- **Matching** — searches YouTube Music and ranks candidates by title/artist similarity (rapidfuzz), duration closeness, and penalties for the wrong version (live, cover, sped-up, remix…). If the best match won't download, the runner-up is tried automatically.
- **Output** — MP3, M4A, or Opus, with embedded cover art, full ID3/MP4/Vorbis tags (incl. album artist, track x/y, date), files grouped into per-album subfolders, and an `.m3u8` playlist in the original order.
- **Lyrics** — fetches time-stamped lyrics from [lrclib.net](https://lrclib.net) (free, no key) and embeds them as real synced lyrics (ID3 `SYLT`) plus a text copy (`USLT`/`©lyr`/`LYRICS`) and a `.lrc` sidecar file. Falls back to plain lyrics when no synced version exists.
- **Resumable** — tracks that are already downloaded are skipped, so re-running after a failure only fetches what's missing.
- **Speed** — downloads run concurrently (`--workers` / the Workers field, default 4).
- **GUI** — a minimal desktop app (`python -m melograb.gui`): paste a link, review the tracklist, hit Download All. Click a red row to see why a track failed; **Retry Failed** re-runs just those. A CLI (`melograb`) is also available.

It never touches Spotify's actual audio streams or DRM — like both tools it inspired, it re-sources audio from YouTube using Spotify (or your CSV) only for metadata/track identity.

## Setup

Requires Python 3.11+.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
brew install ffmpeg      # required for audio conversion
brew install deno        # required by yt-dlp to download from YouTube reliably
brew install python-tk   # required for the GUI to render correctly on macOS
```

> macOS note: the GUI needs a Python built with a modern Tk (8.6+). Apple's bundled Command Line Tools Python ships Tk 8.5, which renders a blank window on Retina displays. Use a Homebrew Python (e.g. `/opt/homebrew/opt/python@3.12/bin/python3.12 -m venv .venv`) instead.

## Usage

### GUI

```bash
python -m melograb.gui
```

Paste a public Spotify playlist or album link, click **Fetch**, review the tracklist, then **Download All**. Your output folder, format and options are remembered between runs.

### CLI

```bash
# playlist or album link
python -m melograb https://open.spotify.com/playlist/XXXXXXXX -f mp3 -o ./downloads

# via CSV (TuneMyMusic / Exportify export or similar)
python -m melograb --csv my_playlist.csv -f m4a -o ./downloads

# more concurrency, skip album folders / lyrics / playlist file
python -m melograb <url> -w 8 --no-album-folders --no-lyrics --no-playlist-file
```

The CLI exits with status 2 if any track failed, and prints the failures grouped by reason.

### Standalone macOS app

```bash
pip install pyinstaller
scripts/build_macos_app.sh   # -> dist/melograb.app
```

ffmpeg and deno aren't bundled; the app finds Homebrew's copies even when launched from Finder.

## Troubleshooting

**Every track fails.** That almost always means YouTube changed something and yt-dlp needs updating:

```bash
pip install -U "yt-dlp[default]"
```

Also make sure ffmpeg and a JavaScript runtime (deno, or node/bun) are installed. The GUI shows a yellow banner at the top if either is missing.

**Seeing why a track failed.** In the GUI, click the red row — the reason appears under the list. Full details (including yt-dlp's own output) are in the log: click **Show Log**, or open `~/Library/Logs/melograb/melograb.log` (macOS) / `~/.cache/melograb/melograb.log` (Linux).

**Album column is empty.** Spotify rate-limits the per-track lookups for big playlists; tracks without a Spotify album fall back to the album name YouTube Music reports.

## Development

```bash
pip install -e ".[dev]"
pytest
```

The tests mock all network access; the tagging tests generate real audio files and need ffmpeg.

## Legal note

Spotify's and YouTube's terms generally don't permit downloading/extracting content outside their own apps' built-in offline features. Whether personal, non-redistributed use crosses into infringement is genuinely unsettled and jurisdiction-dependent. This tool only reads metadata Spotify already publishes to logged-out visitors — it never bypasses a login, paywall, or DRM. For personal use only with content you have the right to access — don't redistribute downloaded files.
