"""Find the best-matching YouTube Music audio for a given Track."""
from __future__ import annotations

import re
import threading

from rapidfuzz import fuzz
from rapidfuzz.utils import default_process
from ytmusicapi import YTMusic

from .track import Track

# Below this score a "match" is more likely a different song than a hit.
MIN_SCORE = 45.0

# Variant markers: if a candidate has one the Spotify title doesn't, it's
# probably the wrong version (live take, cover, fan edit, ...).
_VARIANT_WORDS = (
    "live", "cover", "karaoke", "instrumental", "remix", "acoustic", "sped up",
    "speed up", "slowed", "reverb", "nightcore", "8d",
    "extended", "demo", "remaster", "version", "edit", "mix",
)
# "(feat. X)", "[with X]", or a trailing bare "feat. X" -- but not "with" in
# running text ("Dancing with Myself").
_FEAT_RE = re.compile(
    r"\s*[\(\[]\s*(?:feat\.?|ft\.?|featuring|with)\s[^\)\]]*[\)\]]|\s+(?:feat\.|ft\.|featuring)\s.*$", re.I
)

_THUMB_SIZE_RE = re.compile(r"=w\d+-h\d+")

_local = threading.local()


def bigger_thumbnail(url: str, size: int = 600) -> str:
    """YouTube Music thumbnails are tiny (60-120px) but the same URL serves
    any size."""
    return _THUMB_SIZE_RE.sub(f"=w{size}-h{size}", url)


def best_thumbnail(thumbnails) -> str:
    thumbs = [t for t in thumbnails or [] if isinstance(t, dict) and t.get("url")]
    if not thumbs:
        return ""
    return bigger_thumbnail(max(thumbs, key=lambda t: t.get("width") or 0)["url"])


def client() -> YTMusic:
    # ytmusicapi keeps a requests session per client; give each worker thread
    # its own instead of sharing one across the download pool.
    yt = getattr(_local, "ytmusic", None)
    if yt is None:
        yt = _local.ytmusic = YTMusic()
    return yt


def _norm(text: str) -> str:
    return default_process(text or "")


def _strip_feat(title: str) -> str:
    return _FEAT_RE.sub("", title or "").strip()


def _variant_words(text: str) -> set[str]:
    text = f" {_norm(text)} "
    return {w for w in _VARIANT_WORDS if f" {w} " in text}


def _score(track: Track, result: dict) -> float:
    candidate_title = result.get("title") or ""
    candidate_artists = ", ".join(a.get("name", "") for a in result.get("artists") or [] if a)

    title_score = max(
        fuzz.token_sort_ratio(_norm(track.title), _norm(candidate_title)),
        fuzz.token_sort_ratio(_norm(_strip_feat(track.title)), _norm(_strip_feat(candidate_title))),
    )
    if track.artist:
        # token_set handles "A, B" vs "A" and featured-artist ordering.
        artist_score = fuzz.token_set_ratio(_norm(track.artist), _norm(candidate_artists))
        if artist_score < 50:
            # Video uploads often put the artist in the title ("Artist - Song").
            artist_score = max(artist_score, fuzz.partial_ratio(_norm(track.artist), _norm(candidate_title)))
        score = title_score * 0.6 + artist_score * 0.4
    else:
        score = title_score

    extra_variants = _variant_words(candidate_title) - _variant_words(track.title)
    score -= 12 * len(extra_variants)

    duration_s = result.get("duration_seconds")
    if duration_s and track.duration_ms:
        diff = abs(duration_s - track.duration_ms / 1000)
        if diff <= 3:
            score += 5
        elif diff > 15:
            score -= min(diff - 15, 40) * 1.5

    if result.get("resultType") == "song":
        score += 3  # official audio beats music videos with intros/outros

    return score


def rank_matches(track: Track, limit: int = 3) -> list[dict]:
    """Up to `limit` plausible candidates, best first (each gets a `_score`)."""
    yt = client()
    results = [r for r in yt.search(track.search_query, filter="songs", limit=10) if r.get("videoId")]
    scored = [(r, _score(track, r)) for r in results]

    if not scored or max(s for _, s in scored) < 75:
        videos = [r for r in yt.search(track.search_query, filter="videos", limit=10) if r.get("videoId")]
        scored += [(r, _score(track, r)) for r in videos]

    seen = set()
    ranked = []
    for result, score in sorted(scored, key=lambda p: p[1], reverse=True):
        if score < MIN_SCORE or result["videoId"] in seen:
            continue
        seen.add(result["videoId"])
        ranked.append(dict(result, _score=round(score, 1)))
        if len(ranked) >= limit:
            break
    return ranked


def find_best_match(track: Track) -> dict | None:
    ranked = rank_matches(track, limit=1)
    return ranked[0] if ranked else None
