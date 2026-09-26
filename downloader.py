import os
import re
import uuid
import glob
import asyncio
import aiohttp
import aiofiles
import yt_dlp
from concurrent.futures import ThreadPoolExecutor

# Автоматически подключаем FFmpeg (нужно для облака Render)
try:
    import static_ffmpeg
    static_ffmpeg.add_paths()
except Exception:
    pass

DOWNLOAD_DIR = os.path.join(os.path.dirname(__file__), "downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

executor = ThreadPoolExecutor(max_workers=8)

async def download_tiktok_direct(url: str) -> dict | None:
    """
    Скачивание видео из TikTok без водяных знаков в HD качестве.
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
    }
    timeout = aiohttp.ClientTimeout(total=20)
    connector = aiohttp.TCPConnector(ssl=False)

    async with aiohttp.ClientSession(headers=headers, timeout=timeout, connector=connector) as session:
        # 1. Пробуем TikWM API
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
            print(f"TikWM ошибка: {e}")

        # 2. Пробуем LoveTik
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
            print(f"LoveTik ошибка: {e}")

    return None

async def download_media(url: str) -> dict:
    """
    Универсальная быстрая загрузка видео (YouTube, TikTok, Shorts, Reels и др.)
    """
    # 1. Если это TikTok — используем прямой HD шлюз
    if 'tiktok.com' in url:
        result = await download_tiktok_direct(url)
        if result:
            return result

    # 2. Скачивание YouTube и других видео через yt-dlp с клиентами Android/iOS (обход блокировок хостинга)
    file_id = str(uuid.uuid4())
    output_template = os.path.join(DOWNLOAD_DIR, f"{file_id}.%(ext)s")

    ydl_opts = {
        # Готовое MP4 видео или лучшее видео со звуком
        'format': 'best[ext=mp4]/bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best',
        'merge_output_format': 'mp4',
        'outtmpl': output_template,
        'noplaylist': True,
        'quiet': False,
        'no_warnings': True,
        'max_filesize': 50 * 1024 * 1024,
        'concurrent_fragment_downloads': 5,
        'buffersize': 1024 * 1024,
        'retries': 5,
        'fragment_retries': 5,
        # Обход блокировки серверов Render со стороны YouTube
        'extractor_args': {
            'youtube': {
                'player_client': ['android', 'ios', 'mweb', 'tv_embedded'],
                'player_skip': ['webpage', 'configs']
            }
        },
        'http_headers': {
            'User-Agent': 'com.google.android.youtube/19.10.35 (Linux; U; Android 14; en_US) gzip',
            'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
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
