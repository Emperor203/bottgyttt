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
    """
    Если видео больше 48 МБ (например, часовое видео):
    1. Пробует сжать в целевой размер до 48 МБ через FFmpeg.
    2. Если видео очень длинное (1-2 часа) — аккуратно нарезает на части по 45 МБ.
    """
    size_mb = get_file_size_mb(filepath)
    if size_mb <= 48:
        return [filepath]

    duration = get_video_duration(filepath)
    base, ext = os.path.splitext(filepath)

    # Если длительность до 45 минут — сжимаем битрейтом
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

    # Если видео очень длинное (1-2 часа) — нарезаем на части
    chunk_pattern = f"{base}_part%03d.mp4"
    split_cmd = [
        'ffmpeg', '-y', '-i', filepath,
        '-c', 'copy', '-map', '0',
        '-segment_time', '900',  # куски по 15 минут
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

# ==================== ШЛЮЗЫ ЗАГРУЗКИ YOUTUBE ====================

async def download_youtube_savetube(video_id: str, session: aiohttp.ClientSession) -> dict | None:
    """Шлюз 1: SaveTube API (быстрый, отдает прямые ссылки до 1080p)"""
    endpoints = ["https://cdn51.savetube.me/info", "https://cdn59.savetube.me/info", "https://cdn54.savetube.me/info"]
    yt_url = f"https://www.youtube.com/watch?v={video_id}"
    for ep in endpoints:
        try:
            async with session.post(ep, json={"url": yt_url}, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("status"):
                        vdata = data.get("data", {})
                        title = vdata.get("title", "YouTube Video")
                        formats = vdata.get("video_formats", [])
                        
                        target_url = None
                        for f in formats:
                            if f.get("url"):
                                target_url = f.get("url")
                                break
                        
                        if target_url:
                            file_id = str(uuid.uuid4())
                            filepath = os.path.join(DOWNLOAD_DIR, f"{file_id}.mp4")
                            async with session.get(target_url, timeout=aiohttp.ClientTimeout(total=60)) as v_resp:
                                if v_resp.status == 200:
                                    async with aiofiles.open(filepath, 'wb') as f:
                                        await f.write(await v_resp.read())
                                    return {'files': compress_or_split_video(filepath), 'title': title, 'duration': 0}
        except Exception:
            continue
    return None

async def download_youtube_cobalt(video_id: str, session: aiohttp.ClientSession) -> dict | None:
    """Шлюз 2: Cobalt Multi-Instances (открытый стандарт загрузки)"""
    instances = [
        "https://api.cobalt.tools/",
        "https://cobalt.api.scub3d.com/",
        "https://co.wuk.sh/api/json",
        "https://cobalt-api.kwiatekm.tokyo/"
    ]
    yt_url = f"https://www.youtube.com/watch?v={video_id}"
    for inst in instances:
        try:
            payload = {"url": yt_url, "videoQuality": "720"}
            async with session.post(inst, json=payload, timeout=aiohttp.ClientTimeout(total=8)) as resp:
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
                                return {'files': compress_or_split_video(filepath), 'title': 'YouTube Video', 'duration': 0}
        except Exception:
            continue
    return None

async def download_youtube_invidious(video_id: str, session: aiohttp.ClientSession) -> dict | None:
    """Шлюз 3: Invidious Distributed Network"""
    instances = [
        "https://inv.tux.pizza",
        "https://invidious.nerdvpn.de",
        "https://invidious.f5.si",
        "https://yewtu.be",
        "https://vid.puffyan.us"
    ]
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
                                return {'files': compress_or_split_video(filepath), 'title': title, 'duration': data.get("lengthSeconds", 0)}
        except Exception:
            continue
    return None

async def download_youtube_ddownr(video_id: str, session: aiohttp.ClientSession) -> dict | None:
    """Шлюз 4: Ddownr API"""
    try:
        api_url = f"https://p.oceansaver.in/ajax/download.php?format=720&url=https://www.youtube.com/watch?v={video_id}&api=dfcb6d76f2f6a98d74dda51ad4ac634b"
        async with session.get(api_url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 200:
                data = await resp.json()
                task_id = data.get("id")
                title = data.get("title", "YouTube Video")
                for _ in range(12):
                    await asyncio.sleep(1.5)
                    progress_url = f"https://p.oceansaver.in/ajax/progress.php?id={task_id}"
                    async with session.get(progress_url) as p_resp:
                        if p_resp.status == 200:
                            p_data = await p_resp.json()
                            if p_data.get("progress") == 1000 and p_data.get("download_url"):
                                direct_url = p_data.get("download_url")
                                file_id = str(uuid.uuid4())
                                filepath = os.path.join(DOWNLOAD_DIR, f"{file_id}.mp4")
                                async with session.get(direct_url, timeout=aiohttp.ClientTimeout(total=60)) as v_resp:
                                    if v_resp.status == 200:
                                        async with aiofiles.open(filepath, 'wb') as f:
                                            await f.write(await v_resp.read())
                                        return {'files': compress_or_split_video(filepath), 'title': title, 'duration': 0}
    except Exception:
        pass
    return None

# ==================== ШЛЮЗ TIKTOK ====================

async def download_tiktok_direct(url: str, session: aiohttp.ClientSession) -> dict | None:
    """Прямое скачивание TikTok в HD без водяных знаков"""
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

# ==================== ГЛАВНАЯ ФУНКЦИЯ СКАЧИВАНИЯ ====================

async def download_media(url: str) -> dict:
    """
    Безотказная загрузка через сеть из 5 независимых шлюзов
    """
    headers = {
        'Accept': 'application/json, text/plain, */*',
        'Content-Type': 'application/json',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
    }
    timeout = aiohttp.ClientTimeout(total=90)
    connector = aiohttp.TCPConnector(ssl=False)

    async with aiohttp.ClientSession(headers=headers, timeout=timeout, connector=connector) as session:
        # 1. ТИКТОК
        if 'tiktok.com' in url:
            tt_res = await download_tiktok_direct(url, session)
            if tt_res:
                return tt_res

        # 2. YOUTUBE (Опрос 4 облачных шлюзов без бота Google)
        yt_id = extract_youtube_id(url)
        if yt_id:
            # Пробуем SaveTube
            res = await download_youtube_savetube(yt_id, session)
            if res:
                return res

            # Пробуем Cobalt
            res = await download_youtube_cobalt(yt_id, session)
            if res:
                return res

            # Пробуем Invidious
            res = await download_youtube_invidious(yt_id, session)
            if res:
                return res

            # Пробуем Ddownr
            res = await download_youtube_ddownr(yt_id, session)
            if res:
                return res

    # 3. Резервный локальный yt-dlp
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
                'player_client': ['tv_embedded', 'ios', 'android_creator'],
                'player_skip': ['webpage', 'configs']
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
