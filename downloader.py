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

# ================= 1. PYTUBEFIX ENGINE (С генератором PO-токенов) =================
def download_via_pytubefix_sync(url: str) -> dict | None:
    try:
        from pytubefix import YouTube
        # Используем Android VR / Android Embedded клиент (обход BotGuard и Rate-limits)
        yt = YouTube(url, client='ANDROID_VR')
        stream = yt.streams.filter(progressive=True, file_extension='mp4').order_by('resolution').desc().first()
        if not stream:
            stream = yt.streams.filter(file_extension='mp4').order_by('resolution').desc().first()

        if stream:
            file_id = str(uuid.uuid4())
            out_file = stream.download(output_path=DOWNLOAD_DIR, filename=f"{file_id}.mp4")
            return {
                'files': compress_or_split_video(out_file),
                'title': yt.title or 'YouTube Video',
                'duration': yt.length or 0
            }
    except Exception as e:
        print(f"PyTubeFix ошибка ({e}), пробуем облачные шлюзы...")
    return None

# ================= 2. DDOWNR API =================
async def download_ddownr(video_id: str, session: aiohttp.ClientSession) -> dict | None:
    try:
        url = f"https://www.youtube.com/watch?v={video_id}"
        api_url = f"https://p.oceansaver.in/ajax/download.php?format=720&url={url}&api=dfcb6d76f2f6a98d74dda51ad4ac634b"
        async with session.get(api_url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 200:
                data = await resp.json()
                task_id = data.get("id")
                title = data.get("title", "YouTube Video")
                if task_id:
                    for _ in range(12):
                        await asyncio.sleep(1.5)
                        prog_url = f"https://p.oceansaver.in/ajax/progress.php?id={task_id}"
                        async with session.get(prog_url) as p_resp:
                            if p_resp.status == 200:
                                p_data = await p_resp.json()
                                if p_data.get("progress") == 1000 and p_data.get("download_url"):
                                    d_link = p_data.get("download_url")
                                    file_id = str(uuid.uuid4())
                                    filepath = os.path.join(DOWNLOAD_DIR, f"{file_id}.mp4")
                                    async with session.get(d_link, timeout=aiohttp.ClientTimeout(total=60)) as dl_resp:
                                        if dl_resp.status == 200:
                                            async with aiofiles.open(filepath, 'wb') as f:
                                                await f.write(await dl_resp.read())
                                            return {'files': compress_or_split_video(filepath), 'title': title, 'duration': 0}
    except Exception as e:
        print(f"Ddownr error: {e}")
    return None

# ================= 3. TIKTOK HD API =================
async def download_tiktok(url: str, session: aiohttp.ClientSession) -> dict | None:
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
        print(f"TikTok error: {e}")
    return None

def extract_youtube_id(url: str) -> str:
    match = re.search(r'(?:v=|\/|youtu\.be\/|shorts\/)([a-zA-Z0-9_-]{11})', url)
    if match:
        return match.group(1)
    return url

# ================= ГЛАВНЫЙ МЕНЕДЖЕР =================
async def download_media(url: str) -> dict:
    # 1. ТИКТОК
    if 'tiktok.com' in url:
        headers = {'User-Agent': 'Mozilla/5.0'}
        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(headers=headers, connector=connector) as session:
            tt_res = await download_tiktok(url, session)
            if tt_res:
                return tt_res

    # 2. YOUTUBE ЧЕРЕЗ PYTUBEFIX (Собственная генерация токенов)
    if 'youtu' in url or 'youtube.com' in url:
        loop = asyncio.get_event_loop()
        pt_res = await loop.run_in_executor(executor, download_via_pytubefix_sync, url)
        if pt_res:
            return pt_res

        # Резерв через Ddownr
        yt_id = extract_youtube_id(url)
        headers = {'User-Agent': 'Mozilla/5.0'}
        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(headers=headers, connector=connector) as session:
            d_res = await download_ddownr(yt_id, session)
            if d_res:
                return d_res

    raise Exception("Сервер YouTube временно ограничил запросы с этого IP. Попробуйте повторить через 10 секунд.")
