# melograb

Takes the useful idea from both `spotdl` and `CSVmusic` and merges them into one tool, no Spotify Premium or API keys required:

- **Metadata source** — reads the same public, no-login page data Spotify serves to any logged-out visitor (playlist/album title, tracklist, durations, cover art), with each playlist track enriched against its own public page for its *real* album name and in-album track number. CSV import (e.g. a TuneMyMusic export) works too as a fallback.
- **Matching** — searches YouTube Music and scores candidates by title/artist similarity (rapidfuzz) and duration closeness, not just the first hit.
- **Output** — MP3, M4A, or Opus, with embedded cover art, full ID3/MP4/Vorbis tags, and files grouped into per-album subfolders (`Artist - Title` inside an `<Album>/` folder).
- **Lyrics** — fetches time-stamped lyrics from [lrclib.net](https://lrclib.net) (free, no key) and embeds them as real synced lyrics (ID3 `SYLT`) plus a plain-text fallback (`USLT`) and a `.lrc` sidecar file for players that read those instead.
- **Speed** — downloads run concurrently (`--workers` / the Workers field, default 4).
- **GUI** — a minimal desktop app (`python -m melograb.gui`): paste a link, review the tracklist, hit Download All. A CLI (`melograb`) is also available.

It never touches Spotify's actual audio streams or DRM — like both tools it inspired, it re-sources audio from YouTube using Spotify (or your CSV) only for metadata/track identity.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
brew install ffmpeg      # required for audio conversion/tagging
brew install python-tk   # required for the GUI to render correctly on macOS
```

> macOS note: the GUI needs a Python built with a modern Tk (8.6+). Apple's bundled Command Line Tools Python ships Tk 8.5, which renders a blank window on Retina displays. Use a Homebrew Python (e.g. `/opt/homebrew/opt/python@3.12/bin/python3.12 -m venv .venv`) instead.

## Usage

### GUI

```bash
python -m melograb.gui
```

Paste a public Spotify playlist or album link, click **Fetch**, review the tracklist, then **Download All**. "Group by album" and "Fetch synced lyrics" are on by default.

### CLI

```bash
# playlist or album link
python -m melograb https://open.spotify.com/playlist/XXXXXXXX -f mp3 -o ./downloads

# via CSV (TuneMyMusic export or similar)
python -m melograb --csv my_playlist.csv -f m4a -o ./downloads

# more concurrency, skip album folders / lyrics
python -m melograb <url> -w 8 --no-album-folders --no-lyrics
```

## Legal note

Spotify's and YouTube's terms generally don't permit downloading/extracting content outside their own apps' built-in offline features. Whether personal, non-redistributed use crosses into infringement is genuinely unsettled and jurisdiction-dependent. This tool only reads metadata Spotify already publishes to logged-out visitors — it never bypasses a login, paywall, or DRM. For personal use only with content you have the right to access — don't redistribute downloaded files.
