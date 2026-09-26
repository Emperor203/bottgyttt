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

# Автоподключение FFmpeg
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
    """Получает длительность видео в секундах через ffprobe"""
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
    1. Пробует сжать в целевой размер до 48 МБ
    2. Если сжатие слишком сильно ухудшит качество — разбивает на части (Part 1, Part 2)
    """
    size_mb = get_file_size_mb(filepath)
    if size_mb <= 48:
        return [filepath]

    duration = get_video_duration(filepath)
    base, ext = os.path.splitext(filepath)

    # Если длительность до 45 минут — сжимаем битрейтом
    if 0 < duration <= 2700:
        compressed_path = f"{base}_comp.mp4"
        # Целевой размер 45 МБ в килобитах
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

    # Если видео очень длинное (1-2 часа) — нарезаем на части по 45 МБ
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

async def download_media(url: str) -> dict:
    """
    Универсальная быстрая загрузка видео любой длины (включая 1+ час).
    """
    file_id = str(uuid.uuid4())
    output_template = os.path.join(DOWNLOAD_DIR, f"{file_id}.%(ext)s")

    ydl_opts = {
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'merge_output_format': 'mp4',
        'outtmpl': output_template,
        'noplaylist': True,
        'quiet': True,
        'no_warnings': True,
        'concurrent_fragment_downloads': 8,
        'buffersize': 1024 * 1024,
        'retries': 5,
        'fragment_retries': 5,
        'extractor_args': {
            'youtube': {
                'player_client': ['android', 'ios', 'mweb', 'web'],
                'player_skip': ['webpage', 'configs']
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
            duration = info.get('duration', 0)

            matched_files = glob.glob(os.path.join(DOWNLOAD_DIR, f"{file_id}.*"))
            if not matched_files:
                raise FileNotFoundError("Не удалось найти файл после скачивания.")

            initial_file = matched_files[0]
            # Автоматическая обработка размера для часовых видео
            final_files = compress_or_split_video(initial_file)

            return {
                'files': final_files,
                'title': title,
                'duration': duration
            }

    return await loop.run_in_executor(executor, _download)
