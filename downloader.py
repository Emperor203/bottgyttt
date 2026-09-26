import os
import re
import uuid
import glob
import asyncio
import aiohttp
import aiofiles
import subprocess
from concurrent.futures import ThreadPoolExecutor

try:
    import static_ffmpeg
    static_ffmpeg.add_paths()
except Exception:
    pass

DOWNLOAD_DIR = os.path.join(os.path.dirname(__file__), "downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

executor = ThreadPoolExecutor(max_workers=8)

def get_file_size_mb(filepath: str) -> float:
    try:
        return os.path.getsize(filepath) / (1024 * 1024)
    except Exception:
        return 0

def get_video_duration(filepath: str) -> float:
    try:
        cmd = [
            'ffprobe', '-v', 'error', '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1', filepath
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return float(res.stdout.strip())
    except Exception:
        return 0

def compress_or_split_video(filepath: str) -> list[str]:
    size_mb = get_file_size_mb(filepath)
    if size_mb <= 48:
        return [filepath]

    duration = get_video_duration(filepath)
    base, ext = os.path.splitext(filepath)

    if 0 < duration <= 2700:
        compressed_path = f"{base}_comp.mp4"
        target_total_bitrate = (45 * 8192) / duration
        audio_bitrate = 64
        video_bitrate = max(100, int(target_total_bitrate - audio_bitrate))

        cmd = [
            'ffmpeg', '-y', '-i', filepath,
            '-c:v', 'libx264', '-b:v', f"{video_bitrate}k",
            '-preset', 'veryfast', '-c:a', 'aac', '-b:a', f"{audio_bitrate}k",
            '-vf', 'scale=-2:480', compressed_path
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        if os.path.exists(compressed_path) and get_file_size_mb(compressed_path) <= 49:
            try:
                os.remove(filepath)
            except Exception:
                pass
            return [compressed_path]

    chunk_pattern = f"{base}_part%03d.mp4"
    split_cmd = [
        'ffmpeg', '-y', '-i', filepath,
        '-c', 'copy', '-map', '0',
        '-segment_time', '900',
        '-f', 'segment',
        '-reset_timestamps', '1',
        chunk_pattern
    ]
    subprocess.run(split_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    parts = sorted(glob.glob(f"{base}_part*.mp4"))
    if parts:
        try:
            os.remove(filepath)
        except Exception:
            pass
        return parts

    return [filepath]

def extract_youtube_id(url: str) -> str:
    match = re.search(r'(?:v=|\/|youtu\.be\/|shorts\/)([a-zA-Z0-9_-]{11})', url)
    if match:
        return match.group(1)
    return url

# ================= 1. Y2MATE CLOUD API =================
async def download_y2mate(video_url: str, session: aiohttp.ClientSession) -> dict | None:
    try:
        init_url = "https://www.y2mate.com/mates/analyzeV2/ajax"
        headers = {
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "X-Requested-With": "XMLHttpRequest"
        }
        data = {
            "k_query": video_url,
            "k_page": "home",
            "hl": "en",
            "q_auto": 0
        }
        async with session.post(init_url, data=data, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 200:
                res = await resp.json()
                if res.get("status") == "ok":
                    title = res.get("title", "YouTube Video")
                    v_id = res.get("vid")
                    links = res.get("links", {}).get("mp4", {})
                    
                    # Ищем подходящее качество (720p, 480p, 360p, auto)
                    k_key = None
                    for key in ["auto", "136", "18", "135", "134", "133"]:
                        if key in links:
                            k_key = links[key].get("k")
                            break
                    if not k_key and links:
                        k_key = list(links.values())[0].get("k")

                    if k_key and v_id:
                        conv_url = "https://www.y2mate.com/mates/convertV2/index"
                        conv_data = {"vid": v_id, "k": k_key}
                        async with session.post(conv_url, data=conv_data, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as c_resp:
                            if c_resp.status == 200:
                                c_res = await c_resp.json()
                                if c_res.get("status") == "ok":
                                    d_link = c_res.get("dlink")
                                    if d_link:
                                        file_id = str(uuid.uuid4())
                                        filepath = os.path.join(DOWNLOAD_DIR, f"{file_id}.mp4")
                                        async with session.get(d_link, timeout=aiohttp.ClientTimeout(total=60)) as dl_resp:
                                            if dl_resp.status == 200:
                                                async with aiofiles.open(filepath, 'wb') as f:
                                                    await f.write(await dl_resp.read())
                                                return {'files': compress_or_split_video(filepath), 'title': title, 'duration': 0}
    except Exception as e:
        print(f"Y2Mate ошибка: {e}")
    return None

# ================= 2. SSYOUTUBE API =================
async def download_ssyoutube(video_url: str, session: aiohttp.ClientSession) -> dict | None:
    try:
        api_url = "https://api-wh.sf-helper.com/api/convert"
        payload = {"url": video_url}
        headers = {"User-Agent": "Mozilla/5.0"}
        async with session.post(api_url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 200:
                data = await resp.json()
                title = data.get("meta", {}).get("title", "YouTube Video")
                urls = data.get("url", [])
                target_url = None
                for u in urls:
                    if u.get("ext") == "mp4" and u.get("download_url"):
                        target_url = u.get("download_url")
                        break
                if not target_url and urls:
                    target_url = urls[0].get("download_url")

                if target_url:
                    file_id = str(uuid.uuid4())
                    filepath = os.path.join(DOWNLOAD_DIR, f"{file_id}.mp4")
                    async with session.get(target_url, timeout=aiohttp.ClientTimeout(total=60)) as dl_resp:
                        if dl_resp.status == 200:
                            async with aiofiles.open(filepath, 'wb') as f:
                                await f.write(await dl_resp.read())
                            return {'files': compress_or_split_video(filepath), 'title': title, 'duration': 0}
    except Exception as e:
        print(f"SSYouTube ошибка: {e}")
    return None

# ================= 3. TIKTOK API =================
async def download_tiktok_direct(url: str, session: aiohttp.ClientSession) -> dict | None:
    try:
        tikwm_url = f"https://www.tikwm.com/api/?url={url}&hd=1"
        async with session.get(tikwm_url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get('code') == 0:
                    vdata = data.get('data', {})
                    video_url = vdata.get('hdplay') or vdata.get('play') or vdata.get('wmplay')
                    title = vdata.get('title', 'TikTok Video')
                    if video_url:
                        file_id = str(uuid.uuid4())
                        filepath = os.path.join(DOWNLOAD_DIR, f"{file_id}.mp4")
                        async with session.get(video_url, timeout=aiohttp.ClientTimeout(total=30)) as v_resp:
                            if v_resp.status == 200:
                                async with aiofiles.open(filepath, 'wb') as f:
                                    await f.write(await v_resp.read())
                                return {'files': [filepath], 'title': title, 'duration': 0}
    except Exception as e:
        print(f"TikWM ошибка: {e}")
    return None

# ================= ГЛАВНЫЙ СКАЧИВАТЕЛЬ =================
async def download_media(url: str) -> dict:
    headers = {
        'Accept': 'application/json, text/plain, */*',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
    }
    timeout = aiohttp.ClientTimeout(total=90)
    connector = aiohttp.TCPConnector(ssl=False)

    async with aiohttp.ClientSession(headers=headers, timeout=timeout, connector=connector) as session:
        # ТИКТОК
        if 'tiktok.com' in url:
            tt_res = await download_tiktok_direct(url, session)
            if tt_res:
                return tt_res

        # YOUTUBE
        if 'youtu' in url or 'youtube.com' in url:
            # 1. Y2Mate Gateway
            y2_res = await download_y2mate(url, session)
            if y2_res:
                return y2_res

            # 2. SSYouTube Gateway
            ss_res = await download_ssyoutube(url, session)
            if ss_res:
                return ss_res

    raise Exception("Не удалось скачать видео через облачные шлюзы. Попробуйте другую ссылку.")
