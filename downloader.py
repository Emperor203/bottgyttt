import os
import re
import uuid
import glob
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

def extract_youtube_id(url: str) -> str | None:
    """Точное извлечение 11-значного ID видео из любых ссылок YouTube"""
    patterns = [
        r'(?:https?:\/\/)?(?:www\.)?youtu\.be\/([a-zA-Z0-9_-]{11})',
        r'(?:https?:\/\/)?(?:www\.)?youtube\.com\/(?:watch\?v=|shorts\/|embed\/|v\/)([a-zA-Z0-9_-]{11})',
        r'youtube\.com\/.*[?&]v=([a-zA-Z0-9_-]{11})'
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None

async def download_youtube_via_ddownr(video_id: str) -> dict | None:
    """
    Скачивание YouTube через шлюз Ddownr API (100% без капчи и авторизации)
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
    }
    connector = aiohttp.TCPConnector(ssl=False)
    timeout = aiohttp.ClientTimeout(total=45)

    async with aiohttp.ClientSession(headers=headers, timeout=timeout, connector=connector) as session:
        try:
            api_url = f"https://p.oceansaver.in/ajax/download.php?format=720&url=https://www.youtube.com/watch?v={video_id}&api=dfcb6d76f2f6a98d74dda51ad4ac634b"
            async with session.get(api_url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    task_id = data.get("id")
                    title = data.get("title", "YouTube Video")

                    # Опрашиваем статус готовности (обычно 1-2 секунды)
                    for _ in range(15):
                        await asyncio.sleep(1.5)
                        progress_url = f"https://p.oceansaver.in/ajax/progress.php?id={task_id}"
                        async with session.get(progress_url) as p_resp:
                            if p_resp.status == 200:
                                p_data = await p_resp.json()
                                if p_data.get("progress") == 1000 and p_data.get("download_url"):
                                    direct_download_url = p_data.get("download_url")
                                    file_id = str(uuid.uuid4())
                                    filepath = os.path.join(DOWNLOAD_DIR, f"{file_id}.mp4")

                                    async with session.get(direct_download_url, timeout=aiohttp.ClientTimeout(total=60)) as v_resp:
                                        if v_resp.status == 200:
                                            async with aiofiles.open(filepath, 'wb') as f:
                                                await f.write(await v_resp.read())
                                            final_files = compress_or_split_video(filepath)
                                            return {
                                                'files': final_files,
                                                'title': title,
                                                'duration': 0
                                            }
        except Exception as e:
            print(f"Ddownr API ошибка: {e}")
    return None

async def download_youtube_via_invidious(video_id: str) -> dict | None:
    instances = [
        "https://inv.tux.pizza",
        "https://invidious.nerdvpn.de",
        "https://invidious.f5.si",
        "https://yewtu.be",
        "https://invidious.private.coffee"
    ]
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }
    timeout = aiohttp.ClientTimeout(total=20)
    connector = aiohttp.TCPConnector(ssl=False)

    async with aiohttp.ClientSession(headers=headers, timeout=timeout, connector=connector) as session:
        for inst in instances:
            try:
                api_url = f"{inst}/api/v1/videos/{video_id}"
                async with session.get(api_url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        title = data.get("title", "YouTube Video")
                        streams = data.get("formatStreams", [])
                        
                        target_url = None
                        for s in streams:
                            if s.get("container") == "mp4":
                                target_url = s.get("url")
                                break
                        if not target_url and streams:
                            target_url = streams[0].get("url")

                        if target_url:
                            file_id = str(uuid.uuid4())
                            filepath = os.path.join(DOWNLOAD_DIR, f"{file_id}.mp4")
                            async with session.get(target_url, timeout=aiohttp.ClientTimeout(total=60)) as v_resp:
                                if v_resp.status == 200:
                                    async with aiofiles.open(filepath, 'wb') as f:
                                        await f.write(await v_resp.read())
                                    
                                    final_files = compress_or_split_video(filepath)
                                    return {
                                        'files': final_files,
                                        'title': title,
                                        'duration': data.get("lengthSeconds", 0)
                                    }
            except Exception:
                continue
    return None

async def download_tiktok_direct(url: str) -> dict | None:
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
    Универсальное безотказное скачивание
    """
    # 1. Если это TikTok
    if 'tiktok.com' in url:
        result = await download_tiktok_direct(url)
        if result:
            return result

    # 2. Если это YouTube — пробуем через шлюз Ddownr или Invidious
    yt_id = extract_youtube_id(url)
    if yt_id:
        ddownr_res = await download_youtube_via_ddownr(yt_id)
        if ddownr_res:
            return ddownr_res

        invidious_result = await download_youtube_via_invidious(yt_id)
        if invidious_result:
            return invidious_result

    # 3. Резервный yt-dlp
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
        'retries': 3,
        'fragment_retries': 3,
        'extractor_args': {
            'youtube': {
                'player_client': ['ios', 'android', 'tv_embedded']
            }
        },
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_4_1 like Mac OS X) AppleWebKit/605.1.15',
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
