from shazamio import Shazam

shazam = Shazam()

async def recognize_song(file_path: str) -> dict | None:
    """
    Распознает трек по переданному аудио/голосовому файлу или видео.
    """
    try:
        out = await shazam.recognize(file_path)
        track = out.get('track')
        if not track:
            return None

        title = track.get('title', 'Неизвестно')
        subtitle = track.get('subtitle', 'Неизвестный исполнитель')
        shazam_url = track.get('url', '')
        cover_image = track.get('images', {}).get('coverart', '')
        genres = track.get('genres', {}).get('primary', '')

        return {
            'title': title,
            'artist': subtitle,
            'url': shazam_url,
            'cover': cover_image,
            'genre': genres
        }
    except Exception as e:
        print(f"Ошибка распознавания: {e}")
        return None
