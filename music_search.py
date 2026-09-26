import os
import uuid
import glob
import asyncio
import yt_dlp
from ytmusicapi import YTMusic
from concurrent.futures import ThreadPoolExecutor

try:
    import static_ffmpeg
    static_ffmpeg.add_paths()
except Exception:
    pass

DOWNLOAD_DIR = os.path.join(os.path.dirname(__file__), "downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

ytmusic = YTMusic()
executor = ThreadPoolExecutor(max_workers=6)

async def search_and_download_audio(query: str) -> dict | None:
    """
    Ищет аудиозапись по названию и скачивает MP3 в 320 kbps.
    """
    loop = asyncio.get_event_loop()

    def _search_and_download():
        try:
            video_url = None
            title = query
            artist = "Артист"
            thumbnail = None

            try:
                search_results = ytmusic.search(query, filter="songs")
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
            except Exception:
                pass

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
                'extractor_args': {
                    'youtube': {
                        'player_client': ['android', 'ios']
                    }
                },
                'http_headers': {
                    'User-Agent': 'com.google.android.youtube/19.10.35 (Linux; U; Android 14; en_US) gzip',
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
            print(f"Ошибка поиска музыки ({query}): {e}")
        return None

    return await loop.run_in_executor(executor, _search_and_download)
