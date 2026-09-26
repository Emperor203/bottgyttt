import os
import re
import uuid
import glob
import asyncio
import aiohttp
import aiofiles
import yt_dlp
from concurrent.futures import ThreadPoolExecutor

DOWNLOAD_DIR = os.path.join(os.path.dirname(__file__), "downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

executor = ThreadPoolExecutor(max_workers=8)

async def download_via_cobalt(url: str) -> dict | None:
    """
    Загрузка через Cobalt API (технология топовых ботов для YouTube, TikTok, Instagram, Twitter)
    """
    headers = {
        'Accept': 'application/json',
        'Content-Type': 'application/json',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }
    try:
        timeout = aiohttp.ClientTimeout(total=15)
        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(headers=headers, timeout=timeout, connector=connector) as session:
            payload = {
                "url": url,
                "videoQuality": "1080",
                "filenamePattern": "basic"
            }
            async with session.post("https://api.cobalt.tools/", json=payload) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    status = data.get("status")
                    direct_url = data.get("url")
                    if status in ["tunnel", "redirect", "stream"] and direct_url:
                        file_id = str(uuid.uuid4())
                        filepath = os.path.join(DOWNLOAD_DIR, f"{file_id}.mp4")
                        async with session.get(direct_url, timeout=aiohttp.ClientTimeout(total=45)) as v_resp:
                            if v_resp.status == 200:
                                async with aiofiles.open(filepath, 'wb') as f:
                                    await f.write(await v_resp.read())
                                return {
                                    'filepath': filepath,
                                    'title': 'HD Video'
                                }
    except Exception as e:
        print(f"Cobalt API пропущен ({e}), переключаемся на внутренние шлюзы...")
    return None

async def download_tiktok_direct(url: str) -> dict | None:
    """
    Многоуровневое скачивание видео из TikTok без водяных знаков в HD.
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
    }
    timeout = aiohttp.ClientTimeout(total=20)
    connector = aiohttp.TCPConnector(ssl=False)

    async with aiohttp.ClientSession(headers=headers, timeout=timeout, connector=connector) as session:
        # 1. Сервис LoveTik
        try:
            async with session.post("https://lovetik.com/api/ajax/search", data={"query": url}) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    links = data.get("links", [])
                    video_url = None
                    for link in links:
                        if link.get("t") == "nowm" or "nowatermark" in str(link.get("s", "")).lower():
                            video_url = link.get("a")
                            break
                    if not video_url and links:
                        video_url = links[0].get("a")

                    if video_url:
                        file_id = str(uuid.uuid4())
                        filepath = os.path.join(DOWNLOAD_DIR, f"{file_id}.mp4")
                        async with session.get(video_url) as v_resp:
                            if v_resp.status == 200:
                                async with aiofiles.open(filepath, 'wb') as f:
                                    await f.write(await v_resp.read())
                                return {'filepath': filepath, 'title': data.get('desc', 'TikTok Video')}
        except Exception as e:
            pass

        # 2. Сервис TikWM API
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
                            async with session.get(video_url) as v_resp:
                                if v_resp.status == 200:
                                    async with aiofiles.open(filepath, 'wb') as f:
                                        await f.write(await v_resp.read())
                                    return {'filepath': filepath, 'title': title}
        except Exception as e:
            pass

    return None

async def download_media(url: str) -> dict:
    """
    Универсальная быстрая загрузка видео (TikTok, YouTube, Shorts, Instagram и др.)
    """
    # 1. Пробуем Cobalt
    cobalt_res = await download_via_cobalt(url)
    if cobalt_res:
        return cobalt_res

    # 2. Если TikTok — пробуем прямой HD API
    if 'tiktok.com' in url:
        result = await download_tiktok_direct(url)
        if result:
            return result

    # 3. Резервный комбайн yt-dlp
    file_id = str(uuid.uuid4())
    output_template = os.path.join(DOWNLOAD_DIR, f"{file_id}.%(ext)s")

    ydl_opts = {
        'format': 'bestvideo[ext=mp4][filesize<=48M]+bestaudio[ext=m4a]/bestvideo[filesize<=45M]+bestaudio/best[filesize<=49M]/best',
        'merge_output_format': 'mp4',
        'outtmpl': output_template,
        'noplaylist': True,
        'quiet': True,
        'no_warnings': True,
        'max_filesize': 50 * 1024 * 1024,
        'concurrent_fragment_downloads': 10,
        'buffersize': 1024 * 1024,
        'retries': 3,
        'fragment_retries': 3,
        'socket_timeout': 15,
        'extractor_args': {
            'youtube': {
                'player_client': ['android', 'ios', 'web']
            }
        },
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        }
    }

    loop = asyncio.get_event_loop()

    def _download():
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get('title', 'Видео')
            
            matched_files = glob.glob(os.path.join(DOWNLOAD_DIR, f"{file_id}.*"))
            if matched_files:
                return {
                    'filepath': matched_files[0],
                    'title': title
                }
            
            raise FileNotFoundError("Файл не найден после загрузки.")

    return await loop.run_in_executor(executor, _download)
