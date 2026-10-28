"""Diagnostics for metadata sources (run where Spotify/Apple are reachable).

Prints what Spotify's public track pages / embeds and Apple's iTunes Search
API actually return, so lookups can be fixed against real responses.
"""
import json
import re
import sys

import requests

sys.path.insert(0, ".")
from melograb import apple_music, public_source  # noqa: E402

H = public_source._HEADERS
PLAYLIST = "37i9dQZF1DXcBWIGoYBM5M"


def show(title, value):
    print(f"\n=== {title} ===\n{value}")


entity = public_source._get_entity("playlist", PLAYLIST)
items = entity.get("trackList") or []
show("playlist trackList[0] keys", sorted(items[0].keys()) if items else "EMPTY")
show("playlist trackList[0]", json.dumps(items[0], indent=1)[:1500] if items else "")
track_ids = [i["uri"].rsplit(":", 1)[-1] for i in items[:2] if i.get("uri")]

for tid in track_ids[:1]:
    r = requests.get(f"https://open.spotify.com/track/{tid}", headers=H, timeout=20)
    show(f"track page {tid}", f"HTTP {r.status_code}, {len(r.text)} bytes, final url {r.url}")
    meta = public_source._parse_meta(r.text)
    show("track page meta keys", sorted(meta)[:60])
    show("music:* meta", {k: v for k, v in meta.items() if k.startswith(("music:", "og:"))})
    show("album links in page", sorted(set(re.findall(r"/album/([A-Za-z0-9]{22})", r.text)))[:5])
    show("page head (2000 chars)", r.text[:2000])

    r = requests.get(f"https://open.spotify.com/embed/track/{tid}", headers=H, timeout=20)
    m = public_source._NEXT_DATA_RE.search(r.text)
    show(f"embed track {tid}", f"HTTP {r.status_code}, next_data={'yes' if m else 'no'}")
    if m:
        data = json.loads(m.group(1))
        ent = data.get("props", {}).get("pageProps", {}).get("state", {}).get("data", {}).get("entity", {})
        show("embed track entity keys", sorted(ent.keys()))
        show("embed track entity", json.dumps(ent, indent=1)[:3000])

for params in (
    {"term": "Men I Trust Husk", "media": "music", "entity": "song", "limit": 5, "country": "US"},
    {"term": "Men I Trust Oncle Jazz", "media": "music", "entity": "album", "limit": 3, "country": "US"},
):
    r = requests.get(apple_music.API, params=params, headers=apple_music._HEADERS, timeout=20)
    show(f"itunes {params['entity']}", f"HTTP {r.status_code} {r.headers.get('content-type')}")
    try:
        res = r.json().get("results", [])
        for x in res[:5]:
            print({k: x.get(k) for k in ("wrapperType", "kind", "trackName", "artistName", "collectionName",
                                         "trackTimeMillis", "artworkUrl100", "primaryGenreName")})
    except ValueError:
        print(r.text[:500])

t = public_source.Track(title="Husk", artist="Men I Trust", duration_ms=215_000)
show("apple_music.find_song", apple_music.find_song(t))
