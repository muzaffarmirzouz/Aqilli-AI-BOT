"""Majburiy obuna: foydalanuvchi REQUIRED_CHANNEL ga obuna bo'lmasa, bot ishlamaydi."""
import logging
import time

from aiogram import BaseMiddleware, Bot
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from . import db
from .config import ADMIN_IDS, REQUIRED_CHANNEL

log = logging.getLogger(__name__)
_ok_cache: dict[int, float] = {}   # obuna tasdiqlangan foydalanuvchilar (10 daqiqa kesh)
_warned = {"t": 0.0}
CHECK_CB = "sub:check"


def channel_url() -> str:
    return f"https://t.me/{REQUIRED_CHANNEL.lstrip('@')}"


def sub_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text=f"📢 {REQUIRED_CHANNEL} ga obuna bo'lish", url=channel_url())
    kb.button(text="✅ Obuna bo'ldim", callback_data=CHECK_CB)
    kb.adjust(1)
    return kb.as_markup()


SUB_TEXT = (
    "🔒 <b>Botdan foydalanish uchun kanalimizga obuna bo'ling</b>\n\n"
    f"👉 {REQUIRED_CHANNEL}\n\n"
    "Obuna bo'lgach, «✅ Obuna bo'ldim» tugmasini bosing."
)


async def is_subscribed(bot: Bot, uid: int, use_cache: bool = True) -> bool:
    if not REQUIRED_CHANNEL or uid in ADMIN_IDS:
        return True
    if use_cache and time.time() - _ok_cache.get(uid, 0) < 600:
        return True
    try:
        m = await bot.get_chat_member(REQUIRED_CHANNEL, uid)
    except Exception as e:
        # bot kanalda admin emas yoki kanal topilmadi — hammani bloklab qo'ymaslik uchun o'tkazib yuboramiz
        log.warning("obunani tekshirib bo'lmadi: %s", e)
        if time.time() - _warned["t"] > 3600:
            _warned["t"] = time.time()
            for a in ADMIN_IDS:
                try:
                    await bot.send_message(
                        a, f"⚠️ Majburiy obunani tekshirib bo'lmayapti: {e}\n"
                           f"Botni {REQUIRED_CHANNEL} kanaliga <b>admin</b> qiling.")
                except Exception:
                    pass
        return True
    ok = m.status in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR) or (
        m.status == ChatMemberStatus.RESTRICTED and getattr(m, "is_member", False))
    if ok:
        _ok_cache[uid] = time.time()
    else:
        _ok_cache.pop(uid, None)
    return ok


class SubscribeMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        chat = data.get("event_chat")
        bot: Bot = data["bot"]
        if not REQUIRED_CHANNEL or not user or user.id in ADMIN_IDS:
            return await handler(event, data)
        if chat is not None and chat.type != ChatType.PRIVATE:
            return await handler(event, data)  # kanal/guruhdagi hodisalar tekshirilmaydi
        if isinstance(event, CallbackQuery) and event.data == CHECK_CB:
            return await handler(event, data)
        if await is_subscribed(bot, user.id):
            return await handler(event, data)
        db.upsert_user(user.id, user.full_name)
        if isinstance(event, CallbackQuery):
            await event.answer(f"Avval {REQUIRED_CHANNEL} kanaliga obuna bo'ling", show_alert=True)
            await event.message.answer(SUB_TEXT, reply_markup=sub_kb())
        elif isinstance(event, Message):
            await event.answer(SUB_TEXT, reply_markup=sub_kb())
        return None
