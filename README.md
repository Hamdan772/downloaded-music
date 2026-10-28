# melograb

Takes the useful idea from both `spotdl` and `CSVmusic` and merges them into one tool, no Spotify Premium or API keys required:

- **Sources** — public **Spotify** playlists, albums, songs and artists (top tracks), including `spotify.link` share links; **YouTube / YouTube Music** playlists, albums and videos; **CSV** exports (TuneMyMusic, Exportify, …); or just **type a song** (`mac demarco - chamber of reflection`). Mix several in one go.
- **Metadata** — reads the same public, no-login page data Spotify serves to any logged-out visitor, with each playlist track enriched against its own public page for its *real* album, album artist, cover, release date and in-album track number.
- **Matching** — searches YouTube Music and ranks candidates by title/artist similarity (rapidfuzz), duration closeness, and penalties for the wrong version (live, cover, sped-up, remix…). If the best match won't download, the runner-up is tried automatically. Wrong match? Right-click it in the GUI and paste the right YouTube link.
- **Output** — MP3, M4A, or Opus at your chosen quality, with embedded cover art and full ID3/MP4/Vorbis tags (album artist, track x/y, date, explicit flag, source link), laid out by a [file-name template](#file-layout-templates), plus an `.m3u8` playlist in the original order.
- **Clean audio** — when only a music video is available, non-music intros/outros/skits are cut using [SponsorBlock](https://sponsor.ajay.app)'s community "music: non-music section" data.
- **Lyrics** — fetches time-stamped lyrics from [lrclib.net](https://lrclib.net) (free, no key) and embeds them as real synced lyrics (ID3 `SYLT`) plus a text copy (`USLT`/`©lyr`/`LYRICS`) and a `.lrc` sidecar file. Falls back to plain lyrics. Can also **add lyrics to music you already have**.
- **Resumable** — tracks that are already downloaded are skipped, so re-running after a failure only fetches what's missing.
- **Speed** — downloads run concurrently (`--workers` / the Workers field, default 4).
- **GUI** — a minimal desktop app with light/dark mode: paste links, untick what you don't want, hit Download. Click a red row to see why a track failed; **Retry Failed** re-runs just those. A CLI (`melograb`) is also available.

It never touches Spotify's actual audio streams or DRM — like both tools it inspired, it re-sources audio from YouTube using Spotify (or your CSV) only for metadata/track identity.

## Install

### Mac app (recommended)

Download the DMG for your Mac from the [latest release](https://github.com/Hamdan772/downloaded-music/releases/latest) — `macos-arm64` for Apple Silicon (M1 and newer), `macos-x86_64` for Intel — open it and drag **melograb** into **Applications**. Everything is included: ffmpeg, ffprobe and deno are bundled, so there's nothing else to install.

The app isn't notarized by Apple, so the first launch needs one extra step: right-click melograb in Applications and choose **Open** (on macOS 15+, try to open it once, then go to **System Settings › Privacy & Security** and click **Open Anyway**). After that it opens normally.

YouTube changes often; when the bundled yt-dlp gets old, melograb shows an **Update now** banner that downloads the newest yt-dlp into the app — no reinstall needed.

### From source

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

Paste one or more links (or type a song) and click **Fetch** — a link you just copied is filled in automatically when you switch to melograb. Review the tracklist, untick anything you don't want (click the ☑ header to toggle all, or type in **Filter**), then **Download**.

- **Click a row** to see what it matched on YouTube, or why it failed.
- **Right-click a row** to open it on YouTube, swap in a different YouTube link, download just that track, or show it in Finder.
- **Tools ›** *Add Lyrics to Existing Folder…*, *Update yt-dlp*, *Run Diagnostics…*, *Show Log*. **Appearance ›** light / dark / match system.
- Drop a CSV export on the Dock icon to load it. The Mac stays awake while a download runs, and you get a notification when a long one finishes.

Your output folder, format, layout and options are remembered between runs.

### CLI

```bash
# any mix of links, CSV files and searches
python -m melograb https://open.spotify.com/playlist/XXXXXXXX -f mp3 -o ./downloads
python -m melograb https://music.youtube.com/playlist?list=PLxxxx "dijon - coogie" my_export.csv

# check what everything matches before downloading
python -m melograb <url> --dry-run

# quality, layout, concurrency, and opting out of extras
python -m melograb <url> -q 320 -t "{album_artist}/{album}/{track:02} {title}" -w 8
python -m melograb <url> --no-lyrics --no-sponsorblock --no-playlist-file

# add lyrics to a library you already have (any source)
python -m melograb --add-lyrics ~/Music/Library
```

The CLI exits with status 2 if any track failed, and prints the failures grouped by reason. `python -m melograb --help` lists everything.

### File layout templates

`/` separates folders. Fields: `{title}` `{artist}` `{album}` `{album_artist}` `{track}` `{total_tracks}` `{year}` `{date}` `{playlist}` `{position}`; numbers take a width, e.g. `{track:02}` → `07`.

| Preset | Template | Example |
| --- | --- | --- |
| Album folders (default) | `{album}/{artist} - {title}` | `Oncle Jazz/Men I Trust - Husk.mp3` |
| Flat | `{artist} - {title}` | `Men I Trust - Husk.mp3` |
| Artist / Album / ## Title | `{album_artist}/{album}/{track:02} {title}` | `Men I Trust/Oncle Jazz/07 Husk.mp3` |
| Playlist folder, in order | `{playlist}/{position:03} {artist} - {title}` | `Chill/012 Men I Trust - Husk.mp3` |

### Building the Mac app yourself

```bash
packaging/build_macos.sh     # -> dist/melograb-<version>-macos-<arch>.dmg
```

This fetches static ffmpeg/ffprobe and deno for your CPU, builds `melograb.app` with PyInstaller, runs the app's own self-test, and wraps it in a drag-to-install DMG. GitHub Actions does the same for Apple Silicon and Intel on every relevant push (grab the DMGs from the run's **Artifacts**) and attaches them to a release when you push a `v*` tag:

```bash
git tag v0.4.0 && git push origin v0.4.0
```

To ship a signed and notarized build (no first-launch warning), add these repository secrets: `MACOS_CERTIFICATE` (base64 of your Developer ID Application .p12), `MACOS_CERTIFICATE_PASSWORD`, `MACOS_SIGN_IDENTITY` (e.g. `Developer ID Application: Your Name (TEAMID)`), and for notarization `APPLE_ID`, `APPLE_TEAM_ID`, `APPLE_APP_PASSWORD` (an app-specific password).

## Troubleshooting

**Something's not working.** Run *Tools › Run Diagnostics* in the app (or `python -m melograb --self-test`): it checks every component and can test a real download, and **Copy Report** gives you something to paste into an issue.

**Every track fails.** That almost always means YouTube changed something and yt-dlp needs updating. The GUI checks on startup and offers an **Update now** button; or run:

```bash
pip install -U "yt-dlp[default]"
```

Also make sure ffmpeg and a JavaScript runtime (deno, or node/bun) are installed. The GUI shows a yellow banner at the top if either is missing.

**“YouTube is asking for bot verification.”** YouTube sometimes wants proof you're a person, especially after many downloads. Set **YouTube sign-in** (GUI, top right of the options) to the browser you watch YouTube in, or pass `--cookies-from-browser safari|chrome|firefox|…` in the CLI. melograb then reads that browser's YouTube login locally, the same way yt-dlp does; nothing is sent anywhere but YouTube. On macOS, **Safari** needs melograb to have Full Disk Access (System Settings › Privacy & Security › Full Disk Access), and **Chrome**-family browsers show a one-time keychain prompt — click *Always Allow*. Without sign-in, waiting a while and using **Retry Failed** also usually works.

**One track matched the wrong song.** Right-click it → *Use a Different YouTube Link…* and paste the right one; it's re-downloaded over the wrong file. In the CLI, `--dry-run` shows every match up front.

**Seeing why a track failed.** In the GUI, click the red row — the reason appears under the list. Full details (including yt-dlp's own output) are in the log: *Tools › Show Log*, or open `~/Library/Logs/melograb/melograb.log` (macOS) / `~/.cache/melograb/melograb.log` (Linux).

**Album column is empty.** Spotify rate-limits the per-track lookups for big playlists; tracks without a Spotify album fall back to the album name YouTube Music reports.

## Development

```bash
pip install -e ".[dev]"
pytest
```

The tests mock all network access; the tagging and end-to-end tests generate real audio files and need ffmpeg, and the GUI tests need a display (CI uses `xvfb-run`).

## Legal note

Spotify's and YouTube's terms generally don't permit downloading/extracting content outside their own apps' built-in offline features. Whether personal, non-redistributed use crosses into infringement is genuinely unsettled and jurisdiction-dependent. This tool only reads metadata Spotify already publishes to logged-out visitors — it never bypasses a login, paywall, or DRM. For personal use only with content you have the right to access — don't redistribute downloaded files.
