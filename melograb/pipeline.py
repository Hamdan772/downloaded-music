from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed

from tqdm import tqdm

from . import downloader, lyrics, tag, youtube_match
from .track import Track


def process_one(
    track: Track, out_dir: str, fmt: str, group_by_album: bool = True, fetch_lyrics: bool = True
) -> tuple[Track, str | None, str | None]:
    """Returns (track, output_path_or_None, error_or_None)."""
    try:
        match = youtube_match.find_best_match(track)
        if not match:
            return track, None, "no YouTube match found"
        video_id = match["videoId"]
        if not track.cover_url and match.get("thumbnails"):
            track.cover_url = match["thumbnails"][-1]["url"]

        if fetch_lyrics:
            try:
                track.lyrics_lrc = lyrics.fetch_lrc(track)
            except Exception:  # noqa: BLE001 - lyrics are best-effort
                pass

        dest_dir = os.path.join(out_dir, track.album_folder_name) if group_by_album else out_dir
        path = downloader.download_audio(video_id, dest_dir, track.filename_stem, fmt)
        tag.tag_file(path, track)
        return track, path, None
    except Exception as e:  # noqa: BLE001 - surface all failures per-track
        return track, None, str(e)


def run(
    tracks: list[Track],
    out_dir: str,
    fmt: str = "mp3",
    workers: int = 4,
    group_by_album: bool = True,
    fetch_lyrics: bool = True,
):
    os.makedirs(out_dir, exist_ok=True)
    results = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(process_one, t, out_dir, fmt, group_by_album, fetch_lyrics): t for t in tracks
        }
        for fut in tqdm(as_completed(futures), total=len(futures), desc="Downloading", unit="track"):
            results.append(fut.result())

    ok = [r for r in results if r[1]]
    failed = [r for r in results if not r[1]]
    return ok, failed
