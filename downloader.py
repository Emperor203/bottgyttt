import os
import re
import uuid
import glob
import math
import asyncio
import aiohttp
import aiofiles
import subprocess
import yt_dlp
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
    return os.path.getsize(filepath) / (1024 * 1024)

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
    """
    Автоматическая обработка размера для отправки в Telegram
    """
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

    # Нарезка на части
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

async def download_via_cobalt_gateways(url: str) -> dict | None:
    """
    Скачивание через публичные шлюзы Cobalt (обход YouTube bot verification)
    """
    gateways = [
        "https://api.cobalt.tools/",
        "https://cobalt-api.kwiatekm.tokyo/",
        "https://co.wuk.sh/api/json"
    ]
    headers = {
        'Accept': 'application/json',
        'Content-Type': 'application/json',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }
    timeout = aiohttp.ClientTimeout(total=20)
    connector = aiohttp.TCPConnector(ssl=False)

    async with aiohttp.ClientSession(headers=headers, timeout=timeout, connector=connector) as session:
        for gw in gateways:
            try:
                payload = {"url": url, "videoQuality": "720"}
                async with session.post(gw, json=payload) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        direct_url = data.get("url")
                        if direct_url:
                            file_id = str(uuid.uuid4())
                            filepath = os.path.join(DOWNLOAD_DIR, f"{file_id}.mp4")
                            async with session.get(direct_url, timeout=aiohttp.ClientTimeout(total=60)) as v_resp:
                                if v_resp.status == 200:
                                    async with aiofiles.open(filepath, 'wb') as f:
                                        await f.write(await v_resp.read())
                                    final_files = compress_or_split_video(filepath)
                                    return {
                                        'files': final_files,
                                        'title': 'YouTube Video',
                                        'duration': 0
                                    }
            except Exception:
                continue
    return None

async def download_tiktok_direct(url: str) -> dict | None:
    """
    Прямое скачивание TikTok в HD без водяных знаков
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }
    timeout = aiohttp.ClientTimeout(total=20)
    connector = aiohttp.TCPConnector(ssl=False)

    async with aiohttp.ClientSession(headers=headers, timeout=timeout, connector=connector) as session:
        try:
            tikwm_url = f"https://www.tikwm.com/api/?url={url}&hd=1"
            async with session.get(tikwm_url) as resp:
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

async def download_media(url: str) -> dict:
    """
    Универсальная загрузка с обходом блокировок бота Google/YouTube
    """
    # 1. TikTok
    if 'tiktok.com' in url:
        result = await download_tiktok_direct(url)
        if result:
            return result

    # 2. Обходной шлюз Cobalt для YouTube
    if 'youtu' in url:
        cobalt_res = await download_via_cobalt_gateways(url)
        if cobalt_res:
            return cobalt_res

    # 3. Yt-dlp с клиентами TV Embedded и iOS (не требуют капчи и логина)
    file_id = str(uuid.uuid4())
    output_template = os.path.join(DOWNLOAD_DIR, f"{file_id}.%(ext)s")

    ydl_opts = {
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'merge_output_format': 'mp4',
        'outtmpl': output_template,
        'noplaylist': True,
        'quiet': True,
        'no_warnings': True,
        'concurrent_fragment_downloads': 5,
        'buffersize': 1024 * 1024,
        'retries': 5,
        'fragment_retries': 5,
        # Клиенты, которые никогда не требуют Sign in to confirm you're not a bot
        'extractor_args': {
            'youtube': {
                'player_client': ['tv_embedded', 'ios', 'android_creator'],
                'player_skip': ['webpage', 'configs']
            }
        },
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_4_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Mobile/15E148 Safari/604.1',
        }
    }

    loop = asyncio.get_event_loop()

    def _download():
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get('title', 'Видео')
            duration = info.get('duration', 0)

            matched_files = glob.glob(os.path.join(DOWNLOAD_DIR, f"{file_id}.*"))
            if not matched_files:
                raise FileNotFoundError("Не удалось найти файл после скачивания.")

            initial_file = matched_files[0]
            final_files = compress_or_split_video(initial_file)

            return {
                'files': final_files,
                'title': title,
                'duration': duration
            }

    return await loop.run_in_executor(executor, _download)
