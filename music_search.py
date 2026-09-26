import os
import uuid
import glob
import asyncio
import yt_dlp
from ytmusicapi import YTMusic
from concurrent.futures import ThreadPoolExecutor

DOWNLOAD_DIR = os.path.join(os.path.dirname(__file__), "downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

ytmusic = YTMusic()
executor = ThreadPoolExecutor(max_workers=6)

async def search_and_download_audio(query: str) -> dict | None:
    """
    Ищет аудиозапись по названию (или после Shazam) и скачивает MP3 в 320 kbps с метаданными.
    """
    loop = asyncio.get_event_loop()

    def _search_and_download():
        try:
            # 1. Поиск через YTMusic
            search_results = ytmusic.search(query, filter="songs")
            video_url = None
            title = query
            artist = "Артист"
            thumbnail = None

            if search_results:
                top_song = search_results[0]
                video_id = top_song.get('videoId')
                if video_id:
                    video_url = f"https://www.youtube.com/watch?v={video_id}"
                    title = top_song.get('title', query)
                    artists_list = top_song.get('artists', [])
                    if artists_list:
                        artist = artists_list[0].get('name', '')
                    thumbs = top_song.get('thumbnails', [])
                    if thumbs:
                        thumbnail = thumbs[-1].get('url')

            if not video_url:
                video_url = f"ytsearch1:{query}"

            file_id = str(uuid.uuid4())
            output_template = os.path.join(DOWNLOAD_DIR, f"{file_id}.%(ext)s")

            ydl_opts = {
                'format': 'bestaudio/best',
                'outtmpl': output_template,
                'noplaylist': True,
                'quiet': True,
                'no_warnings': True,
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '320',
                }],
                'http_headers': {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                }
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(video_url, download=True)
                if not title or title == query:
                    title = info.get('title', query)

                matched = glob.glob(os.path.join(DOWNLOAD_DIR, f"{file_id}.*"))
                if matched:
                    return {
                        'filepath': matched[0],
                        'title': title,
                        'artist': artist,
                        'thumbnail': thumbnail
                    }
        except Exception as e:
            print(f"Ошибка поиска/загрузки музыки ({query}): {e}")
        return None

    return await loop.run_in_executor(executor, _search_and_download)
