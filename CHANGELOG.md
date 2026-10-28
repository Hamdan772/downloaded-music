# Changelog

## [0.4.0] - 2026-10-28

The first packaged release: a self-contained Mac app, plus everything that
changed since the original prototype.

### Mac app
- Drag-to-install DMGs for **Apple Silicon** and **Intel** with ffmpeg, ffprobe and deno built in — nothing else to install.
- **Update yt-dlp** from inside the app (YouTube changes often): downloads the newest yt-dlp from PyPI, checksum-verified, and uses it from the next launch.
- **Tools › Run Diagnostics** checks every component and can test a real download; **Copy Report** for bug reports.
- About window, Cmd+Q protection while downloading, drop a CSV on the Dock icon, keeps the Mac awake during downloads, notification when a long run finishes, light/dark mode that follows macOS.

### Fixed
- **Every download failing**: melograb forced yt-dlp onto YouTube clients that now need tokens or a JavaScript solver; it now lets yt-dlp pick clients and gives it a JS runtime and the solver.
- Failures now say *why* (click a red row), with a summary of the most common cause.
- Playlist tracks got the playlist's cover instead of their album's, and their playlist position as the track number.
- Empty Album column for playlist tracks (more robust lookups, YouTube Music fallback).
- Opus files had no cover art; non-JPEG covers weren't converted.

### New
- **Sources:** Spotify songs, artists (top tracks) and `spotify.link` share links; YouTube / YouTube Music playlists, albums and videos; plain-text song search; several inputs at once.
- **YouTube sign-in:** when YouTube asks to “confirm you're not a bot”, melograb offers to use your default browser's YouTube login and retry, in one click.
- **Pick and fix tracks:** tick/untick, filter, see what each song matched, right-click to swap in a different YouTube link.
- **File layout templates** (`{album_artist}/{album}/{track:02} {title}` …) with a live preview; MP3/M4A/Opus at a chosen quality; `.m3u8` playlists.
- **Cleaner audio:** non-music intros/outros cut from music-video matches via SponsorBlock.
- **Lyrics:** synced + plain fallback, and *Add Lyrics to Existing Folder* for music you already have.
- **CLI:** `--dry-run`, `--template`, `--quality`, `--cookies-from-browser`, `--add-lyrics DIR`, `--self-test`.
- Re-runs skip finished songs; Retry Failed; Cancel; settings remembered.
