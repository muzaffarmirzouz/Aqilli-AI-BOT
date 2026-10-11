"""Kanal videolarini ASL SIFATDA yuklab olish (Telegram MTProto, 2 GB gacha).

Oddiy Bot API faqat 20 MB gacha fayl beradi — shuning uchun katta (HD) videolar uchun Telethon ishlatiladi.
Sozlash (Railway): TG_API_ID va TG_API_HASH — https://my.telegram.org → API development tools.
Bot o'sha kanalda admin bo'lishi kerak.
"""
import asyncio
import logging
import os

from .config import BOT_TOKEN, DB_PATH

log = logging.getLogger(__name__)

API_ID = int(os.getenv("TG_API_ID", "0") or 0)
API_HASH = os.getenv("TG_API_HASH", "").strip()
MAX_BYTES = int(os.getenv("VIDEO_MAX_MB", "250")) * 1024 * 1024

_client = None
_lock = asyncio.Lock()


def enabled() -> bool:
    return bool(API_ID and API_HASH)


async def _get_client():
    global _client
    async with _lock:
        if _client is not None and _client.is_connected():
            return _client
        from telethon import TelegramClient  # faqat kerak bo'lganda yuklanadi
        folder = os.path.dirname(DB_PATH) or "."
        os.makedirs(folder, exist_ok=True)
        _client = TelegramClient(os.path.join(folder, "tg_bot_session"), API_ID, API_HASH)
        await _client.start(bot_token=BOT_TOKEN)
        return _client


def _entity(chat):
    """'@kanal' / 'kanal' / -100123... ni Telethon tushunadigan ko'rinishga keltiradi."""
    if isinstance(chat, int):
        return chat
    s = str(chat).strip()
    if s.lstrip("-").isdigit():
        return int(s)
    return s.lstrip("@")


async def download_video(chat, msg_id: int) -> bytes:
    """Kanal postidagi videoni asl sifatda yuklab oladi."""
    client = await _get_client()
    msg = await client.get_messages(_entity(chat), ids=int(msg_id))
    if not msg or not (msg.video or msg.document):
        raise RuntimeError("Postda video topilmadi")
    size = getattr(msg.file, "size", 0) or 0
    if size > MAX_BYTES:
        raise RuntimeError(f"Video {size / 1048576:.0f} MB — {MAX_BYTES // 1048576} MB dan katta")
    data = await client.download_media(msg, file=bytes)
    if not data:
        raise RuntimeError("Video yuklab olinmadi")
    return data
