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

        anime_url = f"https://hianimez.to/watch/{slug}"
        results.append((title, anime_url, slug))

    return results


def get_episodes_list(slug: str):
    """
    Given an anime slug (e.g. "raven-of-the-inner-palace-18168"),
    call:
      GET {ANIWATCH_API_BASE}/anime/{slug}/episodes

    Returns a list of (episode_number, episodeId) tuples, for instance:
      [ ("1", "raven-of-the-inner-palace-18168?ep=1"),
        ("2", "raven-of-the-inner-palace-18168?ep=2"),
        … ]
    """
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
    # 1) pick a working "sub" server
    subs = get_episode_servers(episode_id)
    if not subs:
        logger.warning("No sub‐servers for %s", episode_id)
        return None, None
    server_name = subs[0]["serverName"]

    # 2) fetch the HLS + subtitles
    url = f"{ANIWATCH_API_BASE}/episode/sources"
    params = {
        "animeEpisodeId": episode_id,
        "server":         server_name,
        "category":       "sub"
    }

    resp = requests.get(url, params=params, timeout=10)
    resp.raise_for_status()

    payload = resp.json()
    logger.info("❯❯❯ /episode/sources JSON: %s", payload)
    data     = payload.get("data", {})
    sources  = data.get("sources", [])
    # the old code used `subtitles = data.get("subtitles", [])`
    # but the API might still be returning them under `tracks`
    # the API may return subtitles under `subtitles` or under `tracks`
    subtitle_url = None
    for key, url_key, lang_key in (
        ("subtitles", "url",   "lang"),
        ("tracks",    "file",  "label"),
    ):
        for track in data.get(key, []):
            if track.get(lang_key, "").lower().startswith("english"):
                subtitle_url = track.get(url_key)
                break
        if subtitle_url:
            break

    # If no `hls_link` found, we’ll return None for that part.

    # Next, pick out the English subtitle from `tracks`:
    subtitle_url = None
    for t in subtitles:
        # Each t looks like:
        #   { "lang": "English", "url": "https://…/eng-4.vtt", … }
        if t.get("lang","").lower().startswith("english"):
            subtitle_url = t.get("url")
            break

    return hls_link, subtitle_url
