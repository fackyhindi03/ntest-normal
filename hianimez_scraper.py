# hianimez_scraper.py

import os
import requests
import logging

logger = logging.getLogger(__name__)

# If you have set ANIWATCH_API_BASE in your environment, use that.
# Otherwise default to localhost (useful for local testing).
ANIWATCH_API_BASE = os.getenv(
    "ANIWATCH_API_BASE",
    "http://localhost:4000/api/v2/hianime"
)


def search_anime(query: str):
    """
    Search for anime by name. Returns a list of tuples:
      [ (title, anime_url, animeId), … ]
    where:
      - animeId is the slug (e.g. "raven-of-the-inner-palace-18168")
      - anime_url = "https://hianimez.to/watch/{animeId}"
    """
    url = f"{ANIWATCH_API_BASE}/search"
    params = {"q": query, "page": 1}

    resp = requests.get(url, params=params, timeout=10)
    resp.raise_for_status()

    full_json = resp.json()
    logger.info("AniWatch /search raw JSON: %s", full_json)

    root = full_json.get("data", {})
    anime_list = root.get("animes", [])

    results = []
    for item in anime_list:
        if isinstance(item, str):
            # Sometimes the API returns just a slug string
            slug = item
            title = slug.replace("-", " ").title()
        else:
            # Usually it's a dict with keys: "id", "name", "jname", "poster", etc.
            slug = item.get("id", "")
            title = item.get("name") or item.get("jname") or slug.replace("-", " ").title()

        if not slug:
            continue

        anime_url = f"https://hianime.bz/watch/{slug}"
        results.append((title, anime_url, slug))

    return results


def get_episodes_list(anime_url: str):
    """
    Given a HiAnime page URL (e.g. "https://hianimez.to/watch/raven-of-the-inner-palace-18168"),
    extract slug = "raven-of-the-inner-palace-18168", then call:
      GET /anime/{slug}/episodes

    Returns a list of (episode_number, episodeId) tuples, for instance:
      [ ("1", "raven-of-the-inner-palace-18168?ep=1"),
        ("2", "raven-of-the-inner-palace-18168?ep=2"),
        … ]
    """
    try:
        slug = anime_url.rstrip("/").split("/")[-1]
    except Exception:
        return []

    ep_list_url = f"{ANIWATCH_API_BASE}/anime/{slug}/episodes"
    resp = requests.get(ep_list_url, timeout=10)

    # If the anime has no “/anime/{slug}/episodes” list (e.g. a one‐shot), the API may return 404.
    # In that case, we treat it as a single‐episode fallback:
    if resp.status_code == 404:
        return [("1", f"{slug}?ep=1")]

    resp.raise_for_status()
    full_json = resp.json()

    episodes_data = full_json.get("data", {}).get("episodes", [])
    episodes = []

    for item in episodes_data:
        # Each item has keys like:
        #   "number": <int>,
        #   "title": <string>,
        #   "episodeId": "<slug>?ep=<n>",
        #   …
        ep_num = str(item.get("number", "")).strip()
        ep_id = item.get("episodeId", "").strip()
        if not ep_num or not ep_id:
            continue
        episodes.append((ep_num, ep_id))

    # Sort by numeric episode number just to be safe:
    episodes.sort(key=lambda x: int(x[0]))
    return episodes


def extract_episode_stream_and_subtitle(episode_id: str):
    """
    Given an `episode_id` such as "raven-of-the-inner-palace-18168?ep=1",
    call:
      GET /episode/sources?animeEpisodeId={episode_id}&server=hd-2&category=sub

    By specifying `server="hd-2"`, we force the API to return exactly
    the SUB‐HD2 (1080p) link (if available).
    Returns (hls_link_or_None, subtitle_url_or_None).
    """
    url = f"{ANIWATCH_API_BASE}/episode/sources"
    params = {
        "animeEpisodeId": episode_id,
        "server":          "hd-2",   # <<< force SUB HD-2 (1080p)
        "category":       "sub"
    }

    resp = requests.get(url, params=params, timeout=10)
    resp.raise_for_status()

    data = resp.json().get("data", {})
    sources = data.get("sources", [])
    tracks  = data.get("tracks", [])

    # Since we specifically asked for server=hd-2, the `sources` array
    # should contain only HD-2 entries (if the anime actually has an HD-2 stream).
    hls_link = None
    for s in sources:
        # Each s looks like:
        #   {
        #     "url": "https://…/master.m3u8",
        #     "type": "hls",
        #     "quality": "hd-2",
        #     …
        #   }
        if s.get("type") == "hls" and s.get("url"):
            hls_link = s.get("url")
            break

    # If no `hls_link` found, we’ll return None for that part.

    # Next, pick out the English subtitle from `tracks`:
    subtitle_url = None
    for t in tracks:
        # Each t looks like:
        #   {
        #     "file": "https://…/eng-4.vtt",
        #     "label": "English",
        #     "kind": "captions",
        #     "default": True/False
        #   }
        if t.get("label", "").lower().startswith("english"):
            subtitle_url = t.get("file")
            break

    return hls_link, subtitle_url
