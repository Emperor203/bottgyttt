# 🤖 Telegram Media & Music Bot

Telegram-бот на базе Python (aiogram 3), умеющий:
- 🎬 **Скачивать видео** из TikTok, YouTube и YouTube Shorts.
- 🎵 **Распознавать музыку** по аудиофайлам, голосовым сообщениям (Voice), видео и кружочкам (на базе ShazamIO).

---

## 🚀 Установка и запуск

### 1. Системные требования
Обязательно установите **FFmpeg** (нужен для обработки аудио/видео):
- **Windows**: `winget install Gyan.FFmpeg`
- **Linux (Ubuntu/Debian)**: `sudo apt update && sudo apt install -y ffmpeg`
- **macOS**: `brew install ffmpeg`

### 2. Установка зависимостей Python
Создайте виртуальное окружение и установите библиотеки:

```bash
# Создание venv
python -m venv venv

# Активация venv:
# Windows (PowerShell):
.\venv\Scripts\Activate.ps1
# Linux / macOS:
source venv/bin/activate

# Установка библиотек
pip install -r requirements.txt
```

### 3. Настройка токена бота
1. Скопируйте файл `.env.example` в `.env`:
   ```bash
   cp .env.example .env
   ```
2. Откройте `.env` и вставьте токен вашего бота от [@BotFather](https://t.me/BotFather):
   ```env
   BOT_TOKEN=1234567890:ABCdefGHIjklMNOpqrSTUvwxYZ
   ```

### 4. Запуск бота
```bash
python bot.py
```

---

## 📦 Стек технологий
- [aiogram 3](https://github.com/aiogram/aiogram) — Асинхронный фреймворк Telegram Bot API
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) — Загрузка медиаконтента
- [shazamio](https://github.com/dotX12/ShazamIO) — Асинхронное распознавание треков
- [ffmpeg](https://ffmpeg.org/) — Аудио/видео конвертация
