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
    # slug: "wu-geng-ji-3rd-season-3136"
    # v1 episodes endpoint is under /anime/:slug/episodes
    url = f"{ANIWATCH_API_BASE}/anime/{slug}/episodes"
    resp = requests.get(url, timeout=10)

    # Fallback for single-episode anime
    if resp.status_code == 404:
        return [("1", f"{slug}?ep=1")]

    resp.raise_for_status()
    # drill into the `episodes` array
    raw = resp.json()
    episodes_data = raw.get("data", {}).get("episodes", [])

    episodes = []
    for ep in episodes_data:
        ep_num = str(ep.get("number", "")).strip()
        raw_id = ep.get("episodeId", "").lstrip("/")  # "slug?ep=4"
        if not ep_num or not raw_id:
            continue
        episodes.append((ep_num, raw_id))

    episodes.sort(key=lambda x: int(x[0]))
    return episodes


def extract_episode_stream_and_subtitle(episode_id: str):
    """
    Given an episode_id, fetch HLS stream link and subtitle URL from hianime-API v1.
    Returns (hls_link_or_None, subtitle_url_or_None).
    """
    url = f"{ANIWATCH_API_BASE}/stream"
    params = {
        "id": episode_id,
        "server": "HD-2",
        "type": "sub"
    }

    resp = requests.get(url, params=params, timeout=10)
    resp.raise_for_status()

    data = resp.json().get("data", {})
    stream = data.get("streamingLink", {})

    # HLS playlist URL
    hls_link = stream.get("link", {}).get("file")

    # English subtitle (if available)
    subtitle_url = None
    for track in stream.get("tracks", []):
        if track.get("kind") == "captions" or track.get("label", "").lower().startswith("english"):
            subtitle_url = track.get("file")
            break

    return hls_link, subtitle_url
