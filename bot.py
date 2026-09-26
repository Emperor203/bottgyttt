import os
import re
import asyncio
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart
from aiogram.types import FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton

from downloader import download_media, DOWNLOAD_DIR
from recognizer import recognize_song
from music_search import search_and_download_audio

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
PROXY_URL = os.getenv("PROXY_URL")

session = None
if PROXY_URL:
    from aiogram.client.session.aiohttp import AiohttpSession
    session = AiohttpSession(proxy=PROXY_URL)

bot = Bot(token=BOT_TOKEN if BOT_TOKEN else "DUMMY_TOKEN", session=session)
dp = Dispatcher()

# Кэш Telegram file_id (мгновенная отдача без повторного скачивания)
media_cache = {}

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    await message.answer(
        "👋 **Добро пожаловать в ультра-быстрый медиа-комбайн!**\n\n"
        "🎬 **Скачивание видео (Full HD без водяных знаков):**\n"
        "• Отправьте ссылку на **TikTok, YouTube, Shorts, Reels**.\n\n"
        "🎵 **Поиск и скачивание музыки:**\n"
        "• Напишите **название песни или исполнителя** (например: `Chainsaww Maple`).\n\n"
        "🔍 **Распознавание музыки (Shazam):**\n"
        "• Отправьте **голосовое сообщение**, **кружочек** или **видео** с отрывком трека — бот найдет и сразу пришлет аудиофайл в MP3!\n",
        parse_mode="Markdown"
    )

@dp.message(F.text)
async def handle_text_messages(message: types.Message):
    text = message.text.strip()
    url_match = re.search(r'(https?://[^\s]+)', text)

    # --- 1. ЕСЛИ ОТПРАВЛЕНА ССЫЛКА (ВИДЕО) ---
    if url_match:
        url = url_match.group(0)

        # Проверяем кэш
        if url in media_cache:
            await bot.send_chat_action(chat_id=message.chat.id, action="upload_video")
            await message.reply_video(video=media_cache[url], caption="🎬 **Скачано из кэша (0.05 сек)**", supports_streaming=True)
            return

        await bot.send_chat_action(chat_id=message.chat.id, action="upload_video")
        status_msg = await message.reply("⚡ Загружаю видео в максимальном качестве...")

        try:
            data = await download_media(url)
            filepath = data['filepath']
            title = data['title']

            if os.path.exists(filepath):
                await bot.send_chat_action(chat_id=message.chat.id, action="upload_video")
                video_file = FSInputFile(filepath)
                sent_msg = await message.reply_video(
                    video=video_file,
                    caption=f"🎬 **{title}**",
                    supports_streaming=True,
                    parse_mode="Markdown"
                )
                if sent_msg.video:
                    media_cache[url] = sent_msg.video.file_id
                
                try:
                    os.remove(filepath)
                except Exception:
                    pass
            else:
                await status_msg.edit_text("❌ Не удалось найти скачанный файл.")
        except Exception as e:
            print(f"Ошибка загрузки видео ({url}): {e}")
            await status_msg.edit_text("❌ Не удалось скачать видео (проверьте ссылку или размер до 50 МБ).")
        finally:
            try:
                await status_msg.delete()
            except Exception:
                pass

    # --- 2. ЕСЛИ ОТПРАВЛЕН ТЕКСТ (ПОИСК МУЗЫКИ КАК В VKM6BOT) ---
    else:
        if text.startswith('/'):
            return

        # Проверяем кэш музыки
        cache_key = f"music_{text.lower()}"
        if cache_key in media_cache:
            await bot.send_chat_action(chat_id=message.chat.id, action="upload_voice")
            await message.reply_audio(audio=media_cache[cache_key])
            return

        await bot.send_chat_action(chat_id=message.chat.id, action="upload_voice")
        status_msg = await message.reply(f"🔍 Ищу и скачиваю трек: **{text}**...", parse_mode="Markdown")

        try:
            music_data = await search_and_download_audio(text)
            if music_data and os.path.exists(music_data['filepath']):
                audio_file = FSInputFile(music_data['filepath'])
                sent_audio = await message.reply_audio(
                    audio=audio_file,
                    title=music_data['title'],
                    performer=music_data['artist'],
                    caption=f"🎵 **{music_data['artist']} - {music_data['title']}**",
                    parse_mode="Markdown"
                )
                if sent_audio.audio:
                    media_cache[cache_key] = sent_audio.audio.file_id

                try:
                    os.remove(music_data['filepath'])
                except Exception:
                    pass
            else:
                await status_msg.edit_text(f"😔 Трек по запросу **«{text}»** не найден.")
        except Exception as e:
            print(f"Ошибка поиска музыки: {e}")
            await status_msg.edit_text("❌ Ошибка при поиске аудио.")
        finally:
            try:
                await status_msg.delete()
            except Exception:
                pass

@dp.message(F.voice | F.audio | F.video_note | F.video)
async def handle_audio_recognition(message: types.Message):
    await bot.send_chat_action(chat_id=message.chat.id, action="record_voice")
    status_msg = await message.reply("🔍 Распознаю трек через Shazam...")

    file_id = None
    ext = "mp3"

    if message.voice:
        file_id = message.voice.file_id
        ext = "ogg"
    elif message.audio:
        file_id = message.audio.file_id
        ext = "mp3"
    elif message.video_note:
        file_id = message.video_note.file_id
        ext = "mp4"
    elif message.video:
        file_id = message.video.file_id
        ext = "mp4"

    if not file_id:
        await status_msg.edit_text("❌ Не удалось прочитать файл.")
        return

    temp_path = os.path.join(DOWNLOAD_DIR, f"rec_{message.message_id}_{file_id[:8]}.{ext}")

    try:
        file = await bot.get_file(file_id)
        await bot.download_file(file.file_path, temp_path)

        result = await recognize_song(temp_path)

        if result:
            song_title = result['title']
            song_artist = result['artist']
            search_query = f"{song_artist} - {song_title}"

            text_caption = (
                f"🎉 **Трек успешно распознан!**\n\n"
                f"📌 **Название:** {song_title}\n"
                f"👤 **Исполнитель:** {song_artist}\n"
            )
            if result.get('genre'):
                text_caption += f"🏷 **Жанр:** {result['genre']}\n"

            keyboard = None
            if result.get('url'):
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🎧 Открыть в Shazam", url=result['url'])]
                ])

            if result.get('cover'):
                await message.reply_photo(photo=result['cover'], caption=text_caption, reply_markup=keyboard, parse_mode="Markdown")
            else:
                await message.reply(text=text_caption, reply_markup=keyboard, parse_mode="Markdown")

            # СРАЗУ ИЩЕМ И СКАЧИВАЕМ MP3 ЭТОГО ТРЕКА ДЛЯ ПОЛЬЗОВАТЕЛЯ (КАК В ТОП-БОТАХ)
            await bot.send_chat_action(chat_id=message.chat.id, action="upload_voice")
            music_data = await search_and_download_audio(search_query)
            if music_data and os.path.exists(music_data['filepath']):
                audio_file = FSInputFile(music_data['filepath'])
                await message.reply_audio(
                    audio=audio_file,
                    title=song_title,
                    performer=song_artist,
                    caption=f"📥 **Полная MP3 версия трека:**\n🎵 {song_artist} — {song_title}"
                )
                try:
                    os.remove(music_data['filepath'])
                except Exception:
                    pass
        else:
            await message.reply("😔 К сожалению, не удалось точно определить трек. Попробуйте отправить более громкий или длинный отрывок.")

    except Exception as e:
        print(f"Ошибка распознавания: {e}")
        await message.reply("❌ Произошла ошибка при обработке звука.")
    finally:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass
        try:
            await status_msg.delete()
        except Exception:
            pass

async def start_dummy_server():
    port = os.getenv("PORT")
    if port:
        from aiohttp import web
        app = web.Application()
        app.router.add_get("/", lambda r: web.Response(text="Telegram Bot is alive! 🚀"))
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", int(port))
        await site.start()
        print(f"🌍 Веб-сервер запущен на порту {port} (для Render)")

async def main():
    if not BOT_TOKEN or BOT_TOKEN == "DUMMY_TOKEN":
        print("ОШИБКА: Пожалуйста, укажите BOT_TOKEN в файле .env!")
        return

    # Запуск веб-порта для хостингов
    await start_dummy_server()

    print("🚀 Топовый медиа-бот успешно запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
