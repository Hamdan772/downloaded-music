from __future__ import annotations

import os

import yt_dlp

_CODEC_EXT = {"mp3": "mp3", "m4a": "m4a", "opus": "opus"}


def download_audio(video_id: str, out_dir: str, filename_stem: str, fmt: str = "mp3") -> str:
    if fmt not in _CODEC_EXT:
        raise ValueError(f"Unsupported format: {fmt}")

    os.makedirs(out_dir, exist_ok=True)
    out_template = os.path.join(out_dir, f"{filename_stem}.%(ext)s")

    postprocessors = [
        {
            "key": "FFmpegExtractAudio",
            "preferredcodec": fmt if fmt != "m4a" else "m4a",
            "preferredquality": "0",
        }
    ]
    if fmt == "mp3":
        postprocessors.append({"key": "EmbedThumbnail"})
        postprocessors.append({"key": "FFmpegMetadata"})

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": out_template,
        "quiet": True,
        "no_warnings": True,
        "postprocessors": postprocessors,
        "writethumbnail": fmt == "mp3",
        "noplaylist": True,
        # YouTube's "web" client alone frequently 403s / needs a reload due to
        # SABR streaming rollout; android+tv clients don't require that.
        "extractor_args": {"youtube": {"player_client": ["android", "tv", "web"]}},
    }

    url = f"https://music.youtube.com/watch?v={video_id}"
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

    final_path = os.path.join(out_dir, f"{filename_stem}.{_CODEC_EXT[fmt]}")
    if not os.path.exists(final_path):
        # yt-dlp sometimes keeps the source extension if postprocessing failed
        for f in os.listdir(out_dir):
            if f.startswith(filename_stem):
                return os.path.join(out_dir, f)
        raise FileNotFoundError(f"Expected output not found: {final_path}")
    return final_path
