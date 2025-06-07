import os
import requests

def download_and_rename_subtitle(subtitle_url: str, ep_num: str, cache_dir: str = "subtitles_cache") -> str:
    os.makedirs(cache_dir, exist_ok=True)
    local_filename = f"Episode {ep_num}.vtt"
    local_path = os.path.join(cache_dir, local_filename)
    resp = requests.get(subtitle_url, timeout=15)
    resp.raise_for_status()
    with open(local_path, "wb") as f:
        f.write(resp.content)
    return local_path
