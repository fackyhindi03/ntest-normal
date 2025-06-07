# Updated utils.py
import os
import requests
import subprocess


def download_and_rename_subtitle(subtitle_url: str, ep_num: str, cache_dir: str = "subtitles_cache") -> str:
    os.makedirs(cache_dir, exist_ok=True)
    local_filename = f"Episode_{ep_num}.vtt"
    local_path = os.path.join(cache_dir, local_filename)
    resp = requests.get(subtitle_url, timeout=15)
    resp.raise_for_status()
    with open(local_path, "wb") as f:
        f.write(resp.content)
    return local_path


def download_and_rename_video(
    video_url: str,
    anime_title: str,
    ep_num: str,
    cache_dir: str = "video_cache"
) -> str:
    os.makedirs(cache_dir, exist_ok=True)
    safe_title = anime_title.replace("/", "_").replace(" ", "_")
    filename = f"{safe_title}_E{ep_num}.mp4"
    local_path = os.path.join(cache_dir, filename)

    cmd = [
        "ffmpeg", "-y",
        "-i", video_url,
        "-c", "copy",
        local_path
    ]
    subprocess.run(cmd, check=True)
    return local_path
