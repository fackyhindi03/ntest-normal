# hianimez_scraper.py

import os
import requests
import logging
from urllib.parse import urlparse, parse_qs

logger = logging.getLogger(__name__)

# Base URL for hianime-API v1 service
ANIWATCH_API_BASE = os.getenv(
    "ANIWATCH_API_BASE",
    "http://localhost:3030/api/v1"
)

def search_anime(query: str, page: int = 1):
    """
    Search for anime by name using hianime-API v1.
    Returns a list of tuples: (title, anime_url, animeId)
    """
    url    = f"{ANIWATCH_API_BASE}/search"
    params = {"keyword": query, "page": page}

    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()

    js = resp.json()
    logger.info("hianime-API /search raw JSON: %s", js)

    hits = js.get("data", {}).get("response", [])
    results = []
    for item in hits:
        slug = item.get("id", "").split("?", 1)[0]
        title = (
            item.get("title")
            or item.get("alternativeTitle")
            or slug.replace("-", " ").title()
        )
        anime_url = f"https://hianime.bz/watch/{slug}"
        results.append((title, anime_url, slug))
    return results


def get_episodes_list(slug: str):
    """
    slug is like "wu-geng-ji-3rd-season-3136"
    """
    # ⚠️ Use the v1 episodes endpoint exactly as documented:
    #    GET /api/v1/episodes/{animeId}
    url = f"{ANIWATCH_API_BASE}/episodes/{slug}"
    resp = requests.get(url, timeout=30)

    # one-shot fallback
    if resp.status_code == 404:
        return [("1", f"/watch/{slug}?ep=1")]

    resp.raise_for_status()

    # response.json()["data"] is already a list of episode objects
    eps = resp.json().get("data", [])

    episodes = []
    for ep in eps:
        raw_id = ep.get("id", "").strip()               # e.g. "/watch/slug?ep=4"
        if not raw_id:
            continue

        # parse out the "ep=4" -> "4"
        qs      = urlparse(raw_id).query                # "ep=4"
        ep_nums = parse_qs(qs).get("ep", [])
        if not ep_nums:
            continue
        ep_num = ep_nums[0]

        episodes.append((ep_num, raw_id))

    # sort numerically
    episodes.sort(key=lambda x: int(x[0]))
    return episodes
    
def extract_episode_stream_and_subtitle(episode_id: str):
    """
    episode_id must be the full "/watch/<slug>?ep=<n>" as returned by get_episodes_list.
    """
    # ⚠️ Use the documented stream endpoint:
    #    GET /api/v1/stream?id={id}&server=HD-2&type=sub
    url = f"{ANIWATCH_API_BASE}/stream"
    params = {
        "id":     episode_id,   # keep the leading slash
        "server": "HD-2",       # MUST be uppercase
        "type":   "sub"
    }

    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()

    data   = resp.json().get("data", {})
    stream = data.get("streamingLink", {})

    # pick the HLS link
    hls_link = stream.get("link", {}).get("file")

    # pick the English subtitles (if any)
    subtitle_url = None
    for t in stream.get("tracks", []):
        label = t.get("label", "").lower()
        if t.get("kind") == "captions" or label.startswith("eng"):
            subtitle_url = t.get("file")
            break

    return hls_link, subtitle_url
