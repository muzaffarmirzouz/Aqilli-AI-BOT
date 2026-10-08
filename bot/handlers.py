import asyncio
import calendar
import html
import os
import logging
import re
from datetime import date, timedelta

from aiogram import Bot, F, Router
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (BufferedInputFile, CallbackQuery, ChatMemberUpdated,
                           KeyboardButton, Message, ReplyKeyboardMarkup)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from . import db, jobs, meta, news, prayer_table, sources, subscribe, texts
from .config import AD_CONTACT, ADMIN_IDS, BRAND, REQUIRED_CHANNEL, PRAYER_KEYS, SEND_AT
from .poster import DEFAULT_THEME, THEMES, default_logo
from .regions import DEFAULT_REGION, REGIONS
from .regions import name as region_name

log = logging.getLogger(__name__)
router = Router()
pm = Router()  # faqat shaxsiy chat
pm.message.filter(F.chat.type == ChatType.PRIVATE)
router.include_router(pm)

B_PRAYER, B_WEATHER, B_RATES = "🕌 Namoz vaqtlari", "🌤 Ob-havo", "💵 Valyuta kursi"
B_REGION, B_NOTIFY, B_CHANNEL = "📍 Hududni o'zgartirish", "🔔 Kechki xabar", "📢 Kanalimga ulash"


MULTI = len(REGIONS) > 1  # bitta hudud bo'lsa, hudud tanlash ko'rsatilmaydi


B_ADMIN = "⚙️ Admin panel"


def menu(uid: int | None = None) -> ReplyKeyboardMarkup:
    rows = [
            [KeyboardButton(text=B_PRAYER)],
            [KeyboardButton(text=B_WEATHER), KeyboardButton(text=B_RATES)],
            [KeyboardButton(text=B_REGION), KeyboardButton(text=B_NOTIFY)] if MULTI
            else [KeyboardButton(text=B_NOTIFY)],
            [KeyboardButton(text=B_CHANNEL)],
    ]
    if uid in ADMIN_IDS:
        rows.append([KeyboardButton(text=B_ADMIN)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def region_kb(prefix: str):
    kb = InlineKeyboardBuilder()
    for k, v in REGIONS.items():
        kb.button(text=v[0], callback_data=f"{prefix}:{k}")
    kb.adjust(3)
    return kb.as_markup()


def user_region(uid: int) -> str:
    u = db.get_user(uid)
    return (u["region"] if u and u["region"] in REGIONS else None) or DEFAULT_REGION


def bot_theme() -> str:
    t = db.kv_get("bot_theme")
    return t if t in THEMES else DEFAULT_THEME


async def bot_image(r: str, d: date, data: dict) -> bytes | None:
    """Botning o'z rasmi (foydalanuvchilar uchun) — admin tanlagan rangda."""
    return await asyncio.to_thread(jobs.poster_bytes, r, d, data, BRAND, "",
                                   logo=default_logo(), logo_tint=True, theme=bot_theme())


def today() -> date:
    return jobs.now().date()


def welcome_text(first_name: str) -> str:
    return (
        f"Assalomu alaykum, {first_name}! 👋\n\n"
        "🕌 <b>Namanganliklar.uz — Namoz vaqtlari boti</b>\n\n"
        "Bot nimalar qiladi:\n"
        f"• 🕌 {region_name(DEFAULT_REGION)} namoz vaqtlari — bugun va ertaga, takbir bilan\n"
        "• 🌤 Ob-havo — hozir va keyingi kunlar\n"
        "• 💵 Valyuta kursi — Markaziy bank kursi\n"
        f"• 🔔 Har kuni soat {SEND_AT} da ertangi kun ma'lumotlarini o'zi yuboradi\n"
        "• 📢 Kanalingizga admin qilsangiz, har kuni namoz vaqtlari rasmini o'zi joylaydi\n\n"
        "Kerakli bo'limni tanlang 👇"
    )


# =================== /start va hudud ===================
@pm.message(CommandStart())
async def start(m: Message):
    PENDING.pop(m.from_user.id, None)
    db.upsert_user(m.from_user.id, m.from_user.full_name)
    u = db.get_user(m.from_user.id)
    if not u["region"] and not MULTI:
        db.set_user(m.from_user.id, region=DEFAULT_REGION)
        await m.answer(welcome_text(m.from_user.first_name), reply_markup=menu(m.from_user.id))
        return
    if not u["region"]:
        await m.answer(
            f"Assalomu alaykum, {m.from_user.first_name}! 👋\n\n"
            "Men har kuni namoz vaqtlari, ob-havo va valyuta kursini yuboraman. "
            f"Har kuni soat {SEND_AT} da ertangi kun ma'lumotlarini olasiz.\n\n"
            "📍 Avval hududingizni tanlang:",
            reply_markup=region_kb("reg"),
        )
        return
    await m.answer(welcome_text(m.from_user.first_name), reply_markup=menu(m.from_user.id))


@router.callback_query(F.data == subscribe.CHECK_CB)
async def sub_check(c: CallbackQuery, bot: Bot):
    if not await subscribe.is_subscribed(bot, c.from_user.id, use_cache=False):
        return await c.answer(f"Siz hali {REQUIRED_CHANNEL} kanaliga obuna bo'lmagansiz", show_alert=True)
    await c.answer("Rahmat! ✅")
    try:
        await c.message.delete()
    except Exception:
        pass
    db.upsert_user(c.from_user.id, c.from_user.full_name)
    u = db.get_user(c.from_user.id)
    if not u["region"]:
        db.set_user(c.from_user.id, region=DEFAULT_REGION)
    await c.message.answer(welcome_text(c.from_user.first_name), reply_markup=menu(c.from_user.id))


@pm.message(F.text == B_REGION)
@pm.message(Command("hudud"))
async def change_region(m: Message):
    await m.answer("📍 Hududni tanlang:", reply_markup=region_kb("reg"))


@router.callback_query(F.data.startswith("reg:"))
async def set_region(c: CallbackQuery):
    key = c.data.split(":", 1)[1]
    if key not in REGIONS:
        return await c.answer()
    db.upsert_user(c.from_user.id, c.from_user.full_name)
    db.set_user(c.from_user.id, region=key)
    await c.message.edit_text(f"✅ Hudud: <b>{region_name(key)}</b>")
    await c.message.answer(
        f"Tayyor! Har kuni soat {SEND_AT} da ertangi namoz vaqtlari, ob-havo va dollar kursini yuboraman.",
        reply_markup=menu(c.from_user.id),
    )
    await c.answer()


# =================== Namoz vaqtlari ===================
def prayer_kb(which: str):
    kb = InlineKeyboardBuilder()
    kb.button(text="📅 Bugun" if which != "t" else "• Bugun •", callback_data="pr:t")
    kb.button(text="📅 Ertaga" if which != "n" else "• Ertaga •", callback_data="pr:n")
    kb.button(text="🖼 Rasm ko'rinishida", callback_data=f"pr:img:{which}")
    kb.adjust(2, 1)
    return kb.as_markup()


async def prayer_text(uid: int, which: str) -> str:
    r = user_region(uid)
    d = today() + timedelta(days=1 if which == "n" else 0)
    rec = await sources.get_prayer(r, d)
    return texts.prayer_block(r, d, rec, jobs.now() if which == "t" else None)


@pm.message(F.text == B_PRAYER)
@pm.message(Command("namoz"))
async def prayer(m: Message):
    db.upsert_user(m.from_user.id, m.from_user.full_name)
    await m.answer(await prayer_text(m.from_user.id, "t"), reply_markup=prayer_kb("t"))


@router.callback_query(F.data.in_({"pr:t", "pr:n"}))
async def prayer_switch(c: CallbackQuery):
    which = c.data[-1]
    try:
        await c.message.edit_text(await prayer_text(c.from_user.id, which), reply_markup=prayer_kb(which))
    except Exception:
        pass
    await c.answer()


@router.callback_query(F.data.startswith("pr:img:"))
async def prayer_image(c: CallbackQuery):
    await c.answer("Rasm tayyorlanmoqda…")
    which = c.data.rsplit(":", 1)[1]
    r = user_region(c.from_user.id)
    d = today() + timedelta(days=1 if which == "n" else 0)
    data = await jobs.collect(r, d)
    img = await bot_image(r, d, data)
    if not img:
        return await c.message.answer("⚠️ Bu kun uchun vaqtlar topilmadi.")
    await c.message.answer_photo(BufferedInputFile(img, f"namoz_{d}.jpg"),
                                 caption=f"🕌 {region_name(r)} — {texts.dstr(d)}")


# =================== Ob-havo va kurs ===================
@pm.message(F.text == B_WEATHER)
@pm.message(Command("obhavo"))
async def weather(m: Message):
    r = user_region(m.from_user.id)
    try:
        w = await sources.get_weather(r)
    except Exception:
        w = None
    t = today()
    txt = texts.weather_now(r, w, t) + "\n\n" + texts.weather_day(r, t + timedelta(days=1), w)
    # kechqurun (18:00 dan keyin) — ertangi kun rasmi, aks holda bugungi
    d = t + timedelta(days=1) if jobs.now().hour >= 18 else t
    img = None
    if w:
        img = await asyncio.to_thread(jobs.weather_bytes, r, d, {"w": w}, **jobs.bot_style())
    if img and len(txt) <= 1000:
        await m.answer_photo(BufferedInputFile(img, "obhavo.jpg"), caption=txt)
    else:
        await m.answer(txt)


@pm.message(F.text == B_RATES)
@pm.message(Command("kurs"))
async def rates(m: Message):
    t = today()
    now_r = await sources.get_rates(t)
    msg = texts.rates_block(now_r, "Bugungi valyuta kursi")
    tom = t + timedelta(days=1)
    tom_r = await sources.get_rates(tom)
    if tom_r and tom_r.get("USD", {}).get("date") == tom.strftime("%d.%m.%Y"):
        msg += "\n\n" + texts.rates_block(tom_r, "Ertangi valyuta kursi")
    else:
        msg += "\n\n<i>Ertangi kurs Markaziy bank tomonidan hali e'lon qilinmagan.</i>"
        tom_r = None
    # rasm: ertangi kurs e'lon qilingan bo'lsa — ertangi, aks holda bugungi
    rd, rr_ = (tom, tom_r) if tom_r else (t, now_r)
    img = await asyncio.to_thread(jobs.rates_bytes, DEFAULT_REGION, rd, rr_, **jobs.bot_style())
    if img and len(msg) <= 1000:
        await m.answer_photo(BufferedInputFile(img, "kurs.jpg"), caption=msg)
    else:
        await m.answer(msg)


# =================== Kechki xabar ===================
@pm.message(F.text == B_NOTIFY)
async def toggle_notify(m: Message):
    db.upsert_user(m.from_user.id, m.from_user.full_name)
    u = db.get_user(m.from_user.id)
    new = 0 if u["notify"] else 1
    db.set_user(m.from_user.id, notify=new)
    await m.answer(f"🔔 Har kuni soat {SEND_AT} dagi xabar <b>yoqildi</b>." if new
                   else "🔕 Kechki xabar <b>o'chirildi</b>. Qayta yoqish uchun tugmani yana bosing.")


# =================== Kanalga ulash ===================
@pm.message(F.text == B_CHANNEL)
@pm.message(Command("kanal"))
async def channel_info(m: Message, bot: Bot):
    me = await bot.get_me()
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Kanalga qo'shish",
              url=f"https://t.me/{me.username}?startchannel&admin=post_messages")
    kb.button(text="➕ Guruhga qo'shish",
              url=f"https://t.me/{me.username}?startgroup&admin=post_messages")
    chats = db.active_chats() if m.from_user.id in ADMIN_IDS else db.chats_of(m.from_user.id)
    for c in chats:
        kb.button(text=f"⚙️ {c['title']} — {region_name(c['region'])}", callback_data=f"ch:{c['id']}")
    kb.adjust(1)
    await m.answer(
        "📢 <b>Kanalingizga har kuni avtomatik post</b>\n\n"
        "Botni kanalingizga <b>admin</b> qilib qo'shing (faqat «Xabar joylash» huquqi kifoya). "
        f"Shundan keyin har kuni soat {SEND_AT} da kanalingizga ertangi namoz vaqtlari, "
        "ob-havo va dollar kursi tushirilgan chiroyli rasm chiqadi.\n\n"
        "Rasmda kanalingiz nomi va @username avtomatik yoziladi.",
        reply_markup=kb.as_markup(),
    )


async def is_chat_admin(bot: Bot, chat_id: int, uid: int) -> bool:
    if uid in ADMIN_IDS:
        return True
    try:
        mem = await bot.get_chat_member(chat_id, uid)
        return mem.status in (ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR)
    except Exception:
        return False


def chat_kb(cid: int):
    kb = InlineKeyboardBuilder()
    if MULTI:
        kb.button(text="📍 Hududni o'zgartirish", callback_data=f"chr:{cid}")
    kb.button(text="🎨 Rasm rangi", callback_data=f"cth:{cid}")
    kb.button(text="👁 Ko'rinishni ko'rish", callback_data=f"cpv:{cid}")
    kb.button(text="📣 Reklama matni", callback_data=f"cad:{cid}")
    kb.button(text="📞 Bog'lanish", callback_data=f"cac:{cid}")
    kb.button(text="🖼 Reklama rasmi", callback_data=f"cai:{cid}")
    kb.button(text="🗑 Reklamani tozalash", callback_data=f"cax:{cid}")
    ch = db.get_chat(cid)
    on_w = bool(ch and ch["send_weather"])
    on_r = bool(ch and ch["send_rates"])
    kb.button(text=f"🌤 Ob-havo rasmi: {'✅ yoqilgan' if on_w else '❌ o‘chiq'}", callback_data=f"ctw:{cid}")
    kb.button(text=f"💵 Kurs rasmi: {'✅ yoqilgan' if on_r else '❌ o‘chiq'}", callback_data=f"ctr:{cid}")
    kb.button(text="📤 Hozir kanalga sinov post", callback_data=f"cht:{cid}")
    kb.adjust(2, 2, 2, 1, 1, 1)
    return kb.as_markup()


def chat_summary(ch) -> str:
    th = THEMES.get(ch["theme"] or DEFAULT_THEME, THEMES[DEFAULT_THEME])["title"]
    contact = ch["ad_contact"] if ch["ad_contact"] is not None else AD_CONTACT
    ad = "rasm" if ch["ad_file"] else (ch["ad_text"] or "«Reklamangiz uchun joy»")
    extra = [n for n, f in (("ob-havo", ch["send_weather"]), ("dollar kursi", ch["send_rates"])) if f]
    daily = "namoz vaqtlari" + (" + " + " + ".join(extra) if extra else "")
    return (f"⚙️ <b>{ch['title']}</b>\n"
            f"🕘 Har kuni {SEND_AT} da: {daily}\n"
            f"🎨 Rang: {th}\n📣 Reklama: {ad}\n📞 Bog'lanish: {contact or '—'}")


@router.callback_query(F.data.startswith("ch:"))
async def chat_settings(c: CallbackQuery, bot: Bot):
    cid = int(c.data[3:])
    ch = db.get_chat(cid)
    if not ch or not await is_chat_admin(bot, cid, c.from_user.id):
        return await c.answer("Ruxsat yo'q", show_alert=True)
    await c.message.answer(chat_summary(ch), reply_markup=chat_kb(cid))
    await c.answer()


@router.callback_query(F.data.startswith("chr:"))
async def chat_region_menu(c: CallbackQuery, bot: Bot):
    cid = int(c.data[4:])
    if not await is_chat_admin(bot, cid, c.from_user.id):
        return await c.answer("Ruxsat yo'q", show_alert=True)
    await c.message.answer("📍 Kanal uchun hududni tanlang:", reply_markup=region_kb(f"creg:{cid}"))
    await c.answer()


@router.callback_query(F.data.startswith("creg:"))
async def chat_region_set(c: CallbackQuery, bot: Bot):
    _, cid, key = c.data.split(":")
    cid = int(cid)
    if key not in REGIONS or not await is_chat_admin(bot, cid, c.from_user.id):
        return await c.answer("Ruxsat yo'q", show_alert=True)
    db.set_chat(cid, region=key)
    ch = db.get_chat(cid)
    await c.message.edit_text(f"✅ <b>{ch['title']}</b> uchun hudud: <b>{region_name(key)}</b>",
                              reply_markup=chat_kb(cid))
    await c.answer()


@router.callback_query(F.data.startswith("cht:"))
async def chat_test(c: CallbackQuery, bot: Bot):
    cid = int(c.data[4:])
    if not await is_chat_admin(bot, cid, c.from_user.id):
        return await c.answer("Ruxsat yo'q", show_alert=True)
    await c.answer("Yuborilmoqda…")
    _, n = await jobs.evening(bot, only_chat=cid)
    await c.message.answer("✅ Kanalga sinov post yuborildi." if n else
                           "⚠️ Yuborib bo'lmadi. Bot kanalda admin ekanini va «Xabar joylash» huquqi borligini tekshiring.")


@router.my_chat_member()
async def on_my_member(ev: ChatMemberUpdated, bot: Bot):
    chat = ev.chat
    if chat.type == ChatType.PRIVATE:
        if ev.new_chat_member.status == ChatMemberStatus.KICKED:
            db.set_user(chat.id, active=0)
        return
    st = ev.new_chat_member.status
    if st == ChatMemberStatus.ADMINISTRATOR:
        existing = db.get_chat(chat.id)
        region = existing["region"] if existing and existing["region"] in REGIONS else user_region(ev.from_user.id)
        db.upsert_chat(chat.id, chat.title or "", chat.username, region, ev.from_user.id)
        try:
            await bot.send_message(
                ev.from_user.id,
                f"✅ Bot <b>{chat.title}</b> ga ulandi.\n"
                f"Har kuni soat {SEND_AT} da ertangi namoz vaqtlari rasmi chiqadi.\n"
                f"📍 Hudud: <b>{region_name(region)}</b>."
                + (" O'zgartirish uchun tanlang:" if MULTI else "\nSinab ko'rish uchun 👇"),
                reply_markup=region_kb(f"creg:{chat.id}") if MULTI else chat_kb(chat.id),
            )
        except Exception:
            pass  # admin botga hali /start bosmagan bo'lishi mumkin
    elif st in (ChatMemberStatus.LEFT, ChatMemberStatus.KICKED):
        db.set_chat(chat.id, active=0)
    elif chat.type == ChatType.CHANNEL:
        # kanalda admin huquqi olib tashlansa, post joylay olmaydi
        db.set_chat(chat.id, active=0)


# =================== Kanal rasmi: rang va reklama ===================
PENDING: dict[int, tuple[str, int]] = {}  # foydalanuvchi -> (nima kutilyapti, kanal id)


async def _guard(c: CallbackQuery, bot: Bot) -> int | None:
    cid = int(c.data.split(":", 2)[1])
    if not db.get_chat(cid) or not await is_chat_admin(bot, cid, c.from_user.id):
        await c.answer("Ruxsat yo'q", show_alert=True)
        return None
    return cid


async def send_preview(bot: Bot, uid: int, cid: int):
    ch = db.get_chat(cid)
    d = today() + timedelta(days=1)
    data = await jobs.collect(ch["region"], d)
    items = await jobs.album(ch["region"], d, data, await jobs.chat_style(bot, ch), **jobs.chat_flags(ch))
    if not items:
        return await bot.send_message(uid, "⚠️ Ertangi namoz vaqtlari topilmadi — rasm yasab bo'lmadi.")
    await jobs.send_items(bot, uid, items, "👁 Kanalga har kuni shunday chiqadi.")
    await bot.send_message(uid, chat_summary(ch), reply_markup=chat_kb(cid))


@router.callback_query(F.data.startswith("cth:"))
async def theme_menu(c: CallbackQuery, bot: Bot):
    cid = await _guard(c, bot)
    if cid is None:
        return
    kb = InlineKeyboardBuilder()
    for k, v in THEMES.items():
        kb.button(text=v["title"], callback_data=f"cst:{cid}:{k}")
    kb.adjust(2)
    await c.message.answer("🎨 Rasm rangini tanlang:", reply_markup=kb.as_markup())
    await c.answer()


@router.callback_query(F.data.startswith("cst:"))
async def theme_set(c: CallbackQuery, bot: Bot):
    cid = await _guard(c, bot)
    if cid is None:
        return
    key = c.data.split(":")[2]
    if key not in THEMES:
        return await c.answer()
    db.set_chat(cid, theme=key)
    await c.answer(f"Rang: {THEMES[key]['title']}")
    await send_preview(bot, c.from_user.id, cid)


@router.callback_query(F.data.startswith("cpv:"))
async def preview(c: CallbackQuery, bot: Bot):
    cid = await _guard(c, bot)
    if cid is None:
        return
    await c.answer("Rasm tayyorlanmoqda…")
    await send_preview(bot, c.from_user.id, cid)


_ASK = {
    "cad": ("ad_text", "📣 Reklama joyiga yoziladigan matnni yuboring (qisqa, 1 qator).\n"
                       "Masalan: <i>GTA Avtomoyka — 20% chegirma</i>"),
    "cac": ("ad_contact", "📞 Bog'lanish uchun yozuvni yuboring.\nMasalan: <i>@NamGroup</i> yoki <i>+998 90 123 45 67</i>\n"
                          "Umuman ko'rsatmaslik uchun <b>-</b> yuboring."),
    "cai": ("ad_file", "🖼 Reklama rasmini yuboring (gorizontal, taxminan 4:1 nisbatda yaxshi chiqadi)."),
    "clg": ("logo_file", "🏷 Logotipingizni yuboring.\nEng yaxshisi — shaffof fonli <b>PNG</b>ni <b>fayl</b> qilib yuboring "
                         "(oddiy rasm qilib yuborilsa, Telegram shaffoflikni yo'qotadi)."),
}


@router.callback_query(F.data.regexp(r"^(cad|cac|cai|clg):"))
async def ask_input(c: CallbackQuery, bot: Bot):
    cid = await _guard(c, bot)
    if cid is None:
        return
    kind = c.data.split(":")[0]
    PENDING[c.from_user.id] = (_ASK[kind][0], cid)
    await c.message.answer(_ASK[kind][1] + "\n\nBekor qilish: /start")
    await c.answer()


@router.callback_query(F.data.startswith("clx:"))
async def logo_clear(c: CallbackQuery, bot: Bot):
    cid = await _guard(c, bot)
    if cid is None:
        return
    db.set_chat(cid, logo_file=None)
    await c.answer("Logotip olib tashlandi")
    await send_preview(bot, c.from_user.id, cid)


@router.callback_query(F.data.regexp(r"^(ctw|ctr):"))
async def toggle_extra(c: CallbackQuery, bot: Bot):
    cid = await _guard(c, bot)
    if cid is None:
        return
    field = "send_weather" if c.data.startswith("ctw") else "send_rates"
    ch = db.get_chat(cid)
    new = 0 if ch[field] else 1
    db.set_chat(cid, **{field: new})
    name = "Ob-havo" if field == "send_weather" else "Dollar kursi"
    await c.answer(f"{name} rasmi {'yoqildi' if new else 'o‘chirildi'}")
    ch = db.get_chat(cid)
    try:
        await c.message.edit_text(chat_summary(ch), reply_markup=chat_kb(cid))
    except Exception:
        await c.message.answer(chat_summary(ch), reply_markup=chat_kb(cid))


@router.callback_query(F.data.startswith("cax:"))
async def ad_clear(c: CallbackQuery, bot: Bot):
    cid = await _guard(c, bot)
    if cid is None:
        return
    db.set_chat(cid, ad_text=None, ad_contact=None, ad_file=None)
    await c.answer("Reklama tozalandi")
    await send_preview(bot, c.from_user.id, cid)


@pm.message(F.func(lambda m: m.from_user.id in PENDING and not (m.text or "").startswith("/")))
async def receive_input(m: Message, bot: Bot):
    field, cid = PENDING[m.from_user.id]
    if field in ("ad_file", "logo_file"):
        if m.photo:
            fid = m.photo[-1].file_id
        elif m.document and (m.document.mime_type or "").startswith("image/"):
            fid = m.document.file_id
        else:
            return await m.answer("Iltimos, rasm yuboring (PNG/JPG). Bekor qilish: /start")
        db.set_chat(cid, **{field: fid})
    else:
        txt = (m.text or "").strip()
        if not txt:
            return await m.answer("Matn yuboring. Bekor qilish: /start")
        if len(txt) > 60:
            return await m.answer(f"Juda uzun ({len(txt)} belgi). 60 belgigacha yozing.")
        db.set_chat(cid, **{field: "" if txt == "-" else txt})
    PENDING.pop(m.from_user.id, None)
    await m.answer("✅ Saqlandi.")
    await send_preview(bot, m.from_user.id, cid)


# =================== Admin buyruqlari ===================
admin = Router()
admin.message.filter(F.from_user.id.in_(ADMIN_IDS), F.chat.type == ChatType.PRIVATE)
router.include_router(admin)


@admin.message(Command("stat"))
async def a_stats(m: Message):
    s = db.stats()
    await m.answer(f"👥 Foydalanuvchilar: {s['users']} (faol {s['active']}, kechki xabar {s['notify']})\n"
                   f"📢 Kanal/guruhlar: {s['chats']}")


@admin.message(Command("vaqt"))
async def a_set_time(m: Message, command: CommandObject):
    """/vaqt namangan 2026-10-07 04:58 06:16 12:35 16:01 17:50 19:04"""
    parts = (command.args or "").split()
    try:
        region, ds, *ts = parts
        assert region in REGIONS and len(ts) == 6
        d = date.fromisoformat(ds)
        times = {k: sources._norm_time(v) for k, v in zip(PRAYER_KEYS, ts)}
        assert sources.valid_times(times)
    except Exception:
        return await m.answer("Format: <code>/vaqt namangan 2026-10-07 04:58 06:16 12:35 16:01 17:50 19:04</code>\n"
                              "Vaqtlar tartib bilan: bomdod, quyosh, peshin, asr, shom, xufton.\n"
                              f"Hududlar: {', '.join(REGIONS)}")
    old = db.load_prayer(region, d.isoformat()) or {}
    db.save_prayer(region, d.isoformat(), {"times": times, "hijri": old.get("hijri", "")}, "manual")
    await m.answer("✅ Saqlandi (qo'lda kiritilgan vaqt avtomatik vaqtdan ustun turadi).\n\n"
                   + texts.prayer_block(region, d, db.load_prayer(region, d.isoformat())))


@admin.message(Command("tekshir"))
async def a_check(m: Message, command: CommandObject):
    """/tekshir namangan [2026-10-07]"""
    parts = (command.args or "namangan").split()
    region = parts[0] if parts[0] in REGIONS else DEFAULT_REGION
    d = date.fromisoformat(parts[1]) if len(parts) > 1 else today() + timedelta(days=1)
    rec = await sources.get_prayer(region, d)
    src = (rec or {}).get("source", "—")
    await m.answer(texts.prayer_block(region, d, rec) + f"\n\nManba: <code>{src}</code>")


@admin.message(Command("sinov"))
async def a_test(m: Message, bot: Bot):
    """21:00 dagi xabarni (3 ta rasm) faqat adminning o'ziga yuboradi."""
    n, _ = await jobs.evening(bot, only_user=m.from_user.id)
    if not n:
        await m.answer("⚠️ Yuborib bo'lmadi — loglarni tekshiring.")


@admin.message(Command("hozir_yubor"))
async def a_send_now(m: Message, bot: Bot):
    await m.answer("⏳ Hammaga yuborilmoqda…")
    u, c = await jobs.evening(bot)
    db.kv_set("last_sent", today().isoformat())
    await m.answer(f"✅ {u} foydalanuvchi, {c} kanal.")


@admin.message(Command("xabar"))
async def a_broadcast(m: Message, bot: Bot):
    """Biror xabarga javob (reply) qilib /xabar yozing — hammaga nusxa yuboriladi."""
    src = m.reply_to_message
    if not src:
        return await m.answer("Yubormoqchi bo'lgan xabarga <b>javob (reply)</b> qilib /xabar yozing.")
    ok = 0
    for uid in db.all_active_users():
        r = await jobs._safe(lambda uid=uid: bot.copy_message(uid, m.chat.id, src.message_id),
                             on_forbidden=lambda uid=uid: db.set_user(uid, active=0))
        ok += bool(r)
        await asyncio.sleep(0.04)
    await m.answer(f"✅ {ok} kishiga yuborildi.")


@admin.message(Command("oylik"))
async def a_month(m: Message, command: CommandObject):
    """/oylik 2026-11  + keyingi qatorlarda jadval (islom.uz dan nusxa olsa ham bo'ladi)."""
    args = (command.args or "").strip()
    first, _, body = args.partition("\n")
    mt = re.match(r"(\d{4})-(\d{1,2})$", first.strip())
    if not mt or not body.strip():
        return await m.answer(
            "Format (bitta xabarda):\n<code>/oylik 2026-11\n"
            "1 bomdod quyosh peshin asr shom xufton\n2 ...\n...</code>\n\n"
            "Har qatorda: kun, bomdod, quyosh, peshin, asr, shom, xufton.\n"
            "islom.uz jadvalidan to'g'ridan-to'g'ri nusxa olsangiz ham bo'ladi "
            "(ishroq va tahajjud ustunlari o'zi tashlab yuboriladi)."
        )
    y, mo = int(mt.group(1)), int(mt.group(2))
    days, errs = prayer_table.parse_text(body, y, mo)
    if not days:
        return await m.answer("⚠️ Birorta ham to'g'ri qator topilmadi.\n" + "\n".join(errs[:10]))
    n = prayer_table.save_month(DEFAULT_REGION, y, mo, days, "manual")
    total = calendar.monthrange(y, mo)[1]
    missing = [str(d) for d in range(1, total + 1) if d not in days]
    msg = f"✅ {region_name(DEFAULT_REGION)}: {y}-{mo:02d} uchun {n} kun saqlandi."
    if missing:
        msg += f"\n⚠️ Kiritilmagan kunlar: {', '.join(missing)}"
    if errs:
        msg += "\n⚠️ Xatolar:\n" + "\n".join(errs[:10])
    d1 = min(days)
    msg += "\n\nTekshirish uchun birinchi kun:\n" + texts.prayer_block(
        DEFAULT_REGION, date(y, mo, d1), db.apply_fixed({"times": days[d1], "hijri": ""}))
    await m.answer(msg)


def admin_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="🎨 Bot rasmi rangi", callback_data="adm:theme")
    kb.button(text="👁 Bot rasmini ko'rish", callback_data="adm:pv")
    kb.button(text="📢 Barcha kanallar", callback_data="adm:chats")
    kb.button(text="📊 Statistika", callback_data="adm:stat")
    kb.button(text="📰 Yangilik rasmi", callback_data="adm:news")
    kb.adjust(2)
    return kb.as_markup()


@admin.message(F.text == B_ADMIN)
async def a_panel(m: Message):
    await m.answer(
        f"⚙️ <b>Admin panel</b>\n🎨 Bot rasmi rangi: {THEMES[bot_theme()]['title']}\n\n"
        "Bu rang botdagi «🖼 Rasm ko'rinishida» tugmasi va /sinov rasmlariga qo'llanadi. "
        "Kanallar rangini «📢 Barcha kanallar» orqali alohida o'zgartirasiz.\n\n"
        "Buyruqlar ro'yxati: /admin",
        reply_markup=admin_kb(),
    )


@router.callback_query(F.data.startswith("adm:"))
async def a_panel_cb(c: CallbackQuery, bot: Bot):
    if c.from_user.id not in ADMIN_IDS:
        return await c.answer("Ruxsat yo'q", show_alert=True)
    act = c.data.split(":")[1]
    if act == "theme":
        kb = InlineKeyboardBuilder()
        for k, v in THEMES.items():
            mark = " ✅" if k == bot_theme() else ""
            kb.button(text=v["title"] + mark, callback_data=f"abt:{k}")
        kb.adjust(2)
        await c.message.answer("🎨 Bot rasmlari uchun rang tanlang:", reply_markup=kb.as_markup())
    elif act == "pv":
        await c.answer("Rasm tayyorlanmoqda…")
        return await _bot_preview(c.from_user.id, bot)
    elif act == "chats":
        chats = db.active_chats()
        if not chats:
            await c.message.answer("Hozircha bot hech qaysi kanalga ulanmagan.")
        else:
            kb = InlineKeyboardBuilder()
            for ch in chats:
                th = THEMES.get(ch["theme"] or DEFAULT_THEME, THEMES[DEFAULT_THEME])["title"]
                kb.button(text=f"⚙️ {ch['title']} · {th}", callback_data=f"ch:{ch['id']}")
            kb.adjust(1)
            await c.message.answer(f"📢 Ulangan kanal/guruhlar: {len(chats)}", reply_markup=kb.as_markup())
    elif act == "news":
        await c.message.answer(
            "📰 <b>Yangilik rasmi</b>\n\n"
            "Menga rasm yuboring va izohiga (caption) sarlavhani yozing — Namanganliklar.uz "
            "shablonidagi tayyor rasmni qaytaraman.\n\n"
            "• Urg'u (sariq rang): <code>*so'z*</code>\n"
            "• Teg (qizil yorliq): <code>[Tezkor] Sarlavha</code>\n"
            "• Tayyor rasm ostidagi tugmalar: format (1:1 / 4:5), matnni o'zgartirish, kanalga yuborish.")
    elif act == "stat":
        st = db.stats()
        await c.message.answer(f"👥 Foydalanuvchilar: {st['users']} (faol {st['active']}, kechki xabar {st['notify']})\n"
                               f"📢 Kanal/guruhlar: {st['chats']}")
    await c.answer()


async def _bot_preview(uid: int, bot: Bot):
    r = user_region(uid)
    d = today() + timedelta(days=1)
    data = await jobs.collect(r, d)
    img = await bot_image(r, d, data)
    if not img:
        return await bot.send_message(uid, "⚠️ Ertangi vaqtlar topilmadi — rasm yasab bo'lmadi.")
    await bot.send_photo(uid, BufferedInputFile(img, "bot_rasmi.jpg"),
                         caption=f"👁 Botdagi rasm shunday chiqadi. Rang: {THEMES[bot_theme()]['title']}",
                         reply_markup=admin_kb())


@router.callback_query(F.data.startswith("abt:"))
async def a_set_bot_theme(c: CallbackQuery, bot: Bot):
    if c.from_user.id not in ADMIN_IDS:
        return await c.answer("Ruxsat yo'q", show_alert=True)
    key = c.data.split(":")[1]
    if key not in THEMES:
        return await c.answer()
    db.kv_set("bot_theme", key)
    await c.answer(f"Rang: {THEMES[key]['title']}")
    await _bot_preview(c.from_user.id, bot)


# =================== Yangilik rasmi (sayt uchun shablon) ===================
# Har bir tayyor rasm (xabar) o'z holatiga ega: tugmalar aynan o'sha rasm bilan ishlaydi.
NEWS_MSG: dict[tuple[int, int], dict] = {}   # (admin, xabar_id) -> {src, file_id, text, style, fmt, tag, link}
NEWS_WAIT: dict[int, dict] = {}              # sarlavha kutilayotgan admin -> holat
STYLE_NAMES = {"full": "To'liq rasm"}  # klassik (panel) uslub olib tashlandi
FMT_NAMES = {"kvadrat": "Kvadrat 1:1", "vertikal": "Vertikal 4:5"}
_URL_RE = re.compile(r"https?://\S+")


def _parse_caption(text: str) -> tuple[str, str, str]:
    """'[Tezkor] Sarlavha https://...' -> ('Tezkor', 'Sarlavha', 'https://...')"""
    text = text or ""
    link = ""
    m = _URL_RE.search(text)
    if m:
        link = m.group(0).rstrip(").,")
        text = (text[:m.start()] + text[m.end():]).strip()
    m = re.match(r"\s*\[([^\]]{1,24})\]\s*(.*)", text, re.S)
    tag, title = (m.group(1).strip(), m.group(2).strip()) if m else ("", text.strip())
    return tag, title, link


def _split_body(title: str) -> tuple[str, str]:
    """Birinchi qator — sarlavha, qolgani — to'liq matn (Instagram uchun)."""
    head, _, rest = title.strip().partition("\n")
    return head.strip(), rest.strip()


SOCIAL = [  # (nomi, havola, oddiy emoji, premium emoji ID o'zgaruvchisi)
    ("Telegram", os.getenv("SOCIAL_TELEGRAM", "https://t.me/Namanganliklar_uz"), "✈️", "EMOJI_TELEGRAM"),
    ("Instagram", os.getenv("SOCIAL_INSTAGRAM", ""), "📸", "EMOJI_INSTAGRAM"),
    ("YouTube", os.getenv("SOCIAL_YOUTUBE", ""), "▶️", "EMOJI_YOUTUBE"),
]


def _icon(emoji: str, env: str) -> str:
    """Premium (custom) emoji ID berilgan bo'lsa — premium ikonka, aks holda oddiy emoji."""
    eid = os.getenv(env, "").strip()
    return f'<tg-emoji emoji-id="{eid}">{emoji}</tg-emoji>' if eid.isdigit() else emoji


def _a(url: str, text: str) -> str:
    return f'<a href="{html.escape(url, quote=True)}">{html.escape(text, quote=False)}</a>' if url else \
        html.escape(text, quote=False)


def news_caption(text: str, link: str = "") -> str:
    """Sarlavha + «БАТАФСИЛ ЎҚИШ» havolasi + ijtimoiy tarmoqlar (hammasi qalin)."""
    plain = " ".join((text or "").replace("*", "").split())
    parts = [f"<b>{html.escape(plain, quote=False)}</b>"]
    if link:
        parts.append(f"<b>БАТАФСИЛ ЎҚИШ</b>\n<b>👉{_a(link, link)}</b>")
    socials = " | ".join(_a(url, name) for name, url, _em, _env in SOCIAL)
    parts.append(f"<b>Бизнинг саҳифаларга обуна бўлинг</b>\n<b>{socials}</b>")
    cap = "\n\n".join(parts)
    if len(cap) > 1024:  # Telegram izoh chegarasi — sarlavhani qisqartiramiz
        over = len(cap) - 1024 + 1
        short = html.escape(plain[: max(20, len(plain) - over - 10)], quote=False) + "…"
        cap = "\n\n".join([f"<b>{short}</b>"] + parts[1:])
    return cap


def _plain_title(text: str) -> str:
    return " ".join((text or "").replace("*", "").split())


def _socials_plain() -> str:
    lines = [f"{name}: {url}" for name, url, _e, _v in SOCIAL if url]
    return "Бизнинг саҳифаларга обуна бўлинг\n" + "\n".join(lines) if lines else ""


def fb_caption(st: dict) -> str:
    """Facebook: faqat sarlavha va maqola havolasi."""
    parts = [_plain_title(st.get("text", ""))]
    if st.get("link"):
        parts.append(f"БАТАФСИЛ ЎҚИШ\n👉 {st['link']}")
    return "\n\n".join(p for p in parts if p)


def ig_caption(st: dict) -> str:
    """Instagram: faqat sarlavha + maqolaning to'liq matni (ortiqcha havola va qo'shimchalarsiz)."""
    title = _plain_title(st.get("text", ""))
    body = (st.get("body") or "").strip()
    room = meta.IG_CAPTION_LIMIT - len(title) - 4
    if len(body) > room:
        body = body[: max(0, room - 1)].rsplit(" ", 1)[0] + "…"
    return "\n\n".join(x for x in (title, body) if x)


def news_kb(st: dict):
    kb = InlineKeyboardBuilder()
    other_fmt = "vertikal" if st["fmt"] == "kvadrat" else "kvadrat"
    kb.button(text=f"📐 {FMT_NAMES[other_fmt]}", callback_data=f"nw:fmt:{other_fmt}")
    kb.button(text="✏️ Matnni o'zgartirish", callback_data="nw:text")
    kb.button(text="📤 Kanalga yuborish", callback_data="nwpub")
    kb.adjust(2, 1)
    return kb.as_markup()


def _targets() -> list[tuple]:
    """Yuborish mumkin bo'lgan kanallar: (chat_id, nomi, username)."""
    out, seen = [], set()
    if REQUIRED_CHANNEL:
        u = REQUIRED_CHANNEL.lstrip("@")
        out.append((REQUIRED_CHANNEL, REQUIRED_CHANNEL, u))
        seen.add(u.lower())
    for ch in db.active_chats():
        if ch["username"] and ch["username"].lower() in seen:
            continue
        out.append((ch["id"], ch["title"] or str(ch["id"]), ch["username"]))
    if meta.fb_enabled():
        out.append(("fb", "📘 Facebook", None))
        out.append(("ig", "📸 Instagram", None))
    return out


def _posted_key(c: CallbackQuery) -> str:
    return f"posted:{c.message.chat.id}:{c.message.message_id}"


def _posted(c: CallbackQuery) -> set[str]:
    """Shu yangilik qaysi joylarga yuborilgani (bazada — bot qayta ishga tushsa ham eslab qoladi)."""
    v = db.kv_get(_posted_key(c)) or ""
    return {x for x in v.split(",") if x}


def _mark_posted(c: CallbackQuery, keys):
    db.kv_set(_posted_key(c), ",".join(sorted(_posted(c) | set(keys))))


def _pub_kb(targets, posted: set[str] = frozenset()):
    kb = InlineKeyboardBuilder()
    for i, (cid, title, _u) in enumerate(targets[:20]):
        name = title if cid in ("fb", "ig") else f"📢 {title}"
        if str(cid) in posted:
            name = f"✅ {title}"
        kb.button(text=name, callback_data=f"nwp:{i}")
    left = [t for t in targets if str(t[0]) not in posted]
    if len(left) > 1:
        kb.button(text=f"🌐 Qolganlariga ({len(left)})" if posted else f"🌐 Hammasiga ({len(left)})",
                  callback_data="nwp:all")
    kb.button(text="◀️ Orqaga", callback_data="nwback")
    kb.adjust(1)
    return kb.as_markup()


async def _src_bytes(bot: Bot, st: dict) -> bytes:
    if st.get("src") is None:
        buf = await bot.download(st["file_id"])
        st["src"] = buf.read()
    return st["src"]


async def _send_news(bot: Bot, uid: int, st: dict, intro: str = ""):
    """Rasmni tayyorlab adminga yuboradi va shu xabarga holatni bog'laydi."""
    st["style"] = "full"  # yagona uslub
    src = await _src_bytes(bot, st)
    usd = None
    try:  # bugungi Markaziy bank kursi (20 daqiqa keshlanadi)
        usd = (await sources.get_rates(today())).get("USD", {}).get("rate")
    except Exception:
        pass
    img = await asyncio.to_thread(news.render_news, src, st["text"], st["style"], st["fmt"],
                                  today(), st.get("tag", ""), usd)
    db.kv_set(f"news_pref:{uid}", f"{st['style']}|{st['fmt']}")
    if intro:
        await bot.send_message(uid, intro, disable_web_page_preview=True)
    m = await bot.send_photo(uid, BufferedInputFile(img, f"namanganliklar_{today():%Y%m%d}.jpg"),
                             caption=news_caption(st["text"], st.get("link", "")), reply_markup=news_kb(st))
    NEWS_MSG[(uid, m.message_id)] = st
    while len(NEWS_MSG) > 300:  # xotirani cheklash
        NEWS_MSG.pop(next(iter(NEWS_MSG)))
    return m


def _pref(uid: int) -> tuple[str, str]:
    v = (db.kv_get(f"news_pref:{uid}") or "full|kvadrat").split("|")
    return ("full", v[1] if len(v) > 1 and v[1] in FMT_NAMES else "kvadrat")


async def offer_site_article(bot: Bot, title: str, link: str, image: bytes, body: str = ""):
    """Saytda yangi maqola chiqdi — har bir adminga tayyor rasmni yuboradi (kanalga emas)."""
    for uid in ADMIN_IDS:
        _, fmt = _pref(uid)
        style = "full"
        st = {"site": True, "src": image, "file_id": None, "text": title, "style": style, "fmt": fmt, "tag": "", "link": link,
              "body": body}
        try:
            await _send_news(bot, uid, st, intro="🆕 <b>Saytda yangi maqola</b>")
        except Exception as e:
            log.warning("adminga yangilik yuborilmadi %s: %s", uid, e)


@admin.message(F.photo | (F.document & F.document.mime_type.startswith("image/")))
async def news_photo(m: Message, bot: Bot):
    fid = m.photo[-1].file_id if m.photo else m.document.file_id
    tag, text, link = _parse_caption(m.caption or "")
    text, body = _split_body(text)
    style, fmt = _pref(m.from_user.id)
    st = {"file_id": fid, "src": None, "text": text, "style": style, "fmt": fmt, "tag": tag, "link": link,
          "body": body}
    if not text:
        NEWS_WAIT[m.from_user.id] = st
        return await m.answer("✏️ Endi sarlavhani yozing.\n"
                              "Urg'u: <code>*so'z*</code> · Teg: <code>[Tezkor] Sarlavha</code> · "
                              "Havola: oxiriga maqola manzilini qo'shing")
    await _send_news(bot, m.from_user.id, st)


@admin.message(F.text & F.func(lambda m: m.from_user.id in NEWS_WAIT and not m.text.startswith("/")))
async def news_text(m: Message, bot: Bot):
    st = NEWS_WAIT.pop(m.from_user.id, None)
    if not st:
        return
    tag, text, link = _parse_caption(m.text)
    text, body = _split_body(text)
    st = {**st, "tag": tag or st.get("tag", ""), "text": text, "link": link or st.get("link", ""),
          "body": body or st.get("body", "")}
    await _send_news(bot, m.from_user.id, st)


async def _st_for_meta(c: CallbackQuery, st: dict) -> dict:
    """Facebook/Instagram uchun ma'lumot. Maqola matni yo'q bo'lsa (masalan bot qayta ishga tushgan),
    havoladagi maqoladan saytdan qayta olinadi."""
    if not st.get("text"):
        cap = (c.message.caption or "").split("\n")
        link = next((ln.strip().lstrip("👉").strip() for ln in cap if "/news/" in ln), "")
        st = {**st, "text": cap[0] if cap else "", "link": st.get("link") or link}
    if not (st.get("body") or "").strip():
        mm = re.search(r"/news/(\d+)", st.get("link") or "")
        if mm:
            try:
                import aiohttp
                from . import site
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=25),
                                                 headers={"User-Agent": "Mozilla/5.0"}) as s:
                    _u, _t, _i, body = await site.fetch_article(s, int(mm.group(1)))
                st["body"] = body or ""
            except Exception as e:
                log.warning("maqola matni olinmadi: %s", e)
    return st


def _state(c: CallbackQuery) -> dict | None:
    return NEWS_MSG.get((c.from_user.id, c.message.message_id))


@router.callback_query(F.data.startswith("nw:"))
async def news_cb(c: CallbackQuery, bot: Bot):
    if c.from_user.id not in ADMIN_IDS:
        return await c.answer("Ruxsat yo'q", show_alert=True)
    st = _state(c)
    if not st:
        return await c.answer("Bu rasm eskirgan — rasmni qaytadan yuboring", show_alert=True)
    parts = c.data.split(":")
    if parts[1] == "text":
        NEWS_WAIT[c.from_user.id] = st
        await c.answer()
        return await c.message.answer("✏️ Yangi sarlavhani yozing (havola o'zgarmaydi):")
    await c.answer("Tayyorlanmoqda…")
    await _send_news(bot, c.from_user.id, {**st, parts[1]: parts[2]})


@router.callback_query(F.data.in_({"nwpub", "nwback"}) | F.data.startswith("nwp:") | F.data.startswith("nwy:"))
async def news_publish(c: CallbackQuery, bot: Bot):
    """Tayyor rasmni kanalga forward belgisisiz yuborish (faqat tasdiqlangandan keyin)."""
    if c.from_user.id not in ADMIN_IDS:
        return await c.answer("Ruxsat yo'q", show_alert=True)
    st = _state(c) or {"style": "full", "fmt": "kvadrat", "text": ""}
    posted = _posted(c)
    if c.data == "nwback":
        if _state(c):
            await c.message.edit_reply_markup(reply_markup=news_kb(st))
        return await c.answer()
    if c.data == "nwpub":
        st["targets"] = _targets()
        if not st["targets"]:
            return await c.answer("Bot hech qaysi kanalga ulanmagan", show_alert=True)
        await c.message.edit_reply_markup(reply_markup=_pub_kb(st["targets"], posted))
        return await c.answer("Qaysi kanalga yuboramiz?")
    targets = st.get("targets") or _targets()
    st["targets"] = targets
    key = c.data.split(":", 1)[1]
    if key == "all":
        chosen = [t for t in targets if str(t[0]) not in posted]
        if not chosen:
            return await c.answer("✅ Bu yangilik hamma joyga avval yuborilgan", show_alert=True)
    else:
        chosen = [targets[int(key)]] if key.isdigit() and int(key) < len(targets) else []
        if not chosen:
            return await c.answer("Kanal topilmadi, qaytadan urinib ko'ring", show_alert=True)
        if str(chosen[0][0]) in posted:
            return await c.answer(f"✅ Bu yangilik {chosen[0][1]} ga avval yuborilgan", show_alert=True)
    if c.data.startswith("nwp:"):
        kb = InlineKeyboardBuilder()
        kb.button(text="✅ Ha, yuborish", callback_data=f"nwy:{key}")
        kb.button(text="◀️ Bekor qilish", callback_data="nwpub")
        kb.adjust(2)
        await c.message.edit_reply_markup(reply_markup=kb.as_markup())
        return await c.answer(f"{', '.join(t[1] for t in chosen)} ga yuborilsinmi?")
    await c.answer("Yuborilmoqda…")
    ok, fail, done = [], [], []
    for chat_id, title, uname in chosen:
        try:
            if chat_id in ("fb", "ig"):
                img = (await bot.download(c.message.photo[-1].file_id)).read()
                mst = await _st_for_meta(c, st)
                url = await (meta.post_facebook(img, fb_caption(mst)) if chat_id == "fb"
                             else meta.post_instagram(img, ig_caption(mst)))
                ok.append(f"✅ <a href=\"{html.escape(url, quote=True)}\">{html.escape(title)}</a>")
                done.append(str(chat_id))
                continue
            # forward belgisisiz, tugmalarsiz toza post: shu rasm + shu izoh (sarlavha va havola)
            m = await bot.send_photo(chat_id, c.message.photo[-1].file_id, caption=c.message.html_text)
            link = f"https://t.me/{uname}/{m.message_id}" if uname else ""
            ok.append(f"✅ <a href=\"{link}\">{html.escape(title)}</a>" if link else f"✅ {html.escape(title)}")
            done.append(str(chat_id))
        except Exception as e:
            fail.append(f"❌ {html.escape(title)} — {html.escape(str(e))[:80]}")
        await asyncio.sleep(0.1)
    if done:
        _mark_posted(c, done)
    # tugmalar joyida qoladi — yuborilganlar ✅ bilan belgilanadi
    try:
        await c.message.edit_reply_markup(reply_markup=_pub_kb(targets, _posted(c)))
    except Exception:
        pass
    await c.message.answer("\n".join(ok + fail) or "Hech narsa yuborilmadi", disable_web_page_preview=True)


@admin.message(Command("matn"))
async def a_matn(m: Message, command: CommandObject):
    """Saytdagi maqoladan bot qanday matn olayotganini ko'rsatadi: /matn https://namanganliklar.uz/news/123"""
    import aiohttp
    from . import site
    mm = re.search(r"/news/(\d+)", command.args or "")
    if not mm:
        await m.answer("Masalan: <code>/matn https://namanganliklar.uz/news/28247</code>")
        return
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=25),
                                         headers={"User-Agent": "Mozilla/5.0"}) as s:
            _url, title, _img, body = await site.fetch_article(s, int(mm.group(1)))
    except Exception as e:
        await m.answer(f"❌ Xato: {html.escape(str(e))}")
        return
    body = body or "(matn topilmadi)"
    await m.answer(f"<b>{html.escape(title)}</b>\n\n{html.escape(body[:3500])}\n\n"
                   f"<i>{len(body)} belgi</i>")


@admin.message(Command("token"))
async def a_token(m: Message, command: CommandObject):
    """Graph API Explorer'dagi tokenni muddatsiz sahifa tokeniga aylantirish: /token EAAB..."""
    tok = (command.args or "").strip()
    try:
        await m.delete()  # token chatda ochiq qolmasin
    except Exception:
        pass
    if not tok:
        return await m.answer(
            "Graph API Explorer → <b>Generate Access Token</b> (Namanganliklar.Uz, Namangam va Instagram'ni "
            "belgilang) → tokenni nusxalab shunday yuboring:\n<code>/token EAAB...</code>\n\n"
            "Extend qilish shart emas — bot o'zi muddatsiz qiladi.")
    msg = await m.answer("⏳ Token tekshirilmoqda…")
    try:
        res = await meta.set_from_user_token(tok)
        await msg.edit_text(res + "\n\nRailway'ni o'zgartirish shart emas — bot yangi tokenni saqlab oldi.")
    except Exception as e:
        await msg.edit_text(f"❌ {html.escape(str(e))}")


@admin.message(Command("meta"))
async def a_meta(m: Message):
    """Facebook/Instagram ulanishini tekshirish."""
    try:
        await m.answer(await meta.check())
    except Exception as e:
        await m.answer(f"❌ Xato: {html.escape(str(e))}")


@admin.message(Command("sayt"))
async def a_site(m: Message, command: CommandObject):
    """/sayt on | off — saytdagi yangi maqolalarni kuzatish."""
    from . import site
    arg = (command.args or "").strip().lower()
    if arg in ("on", "off"):
        db.kv_set("site_watch", arg)
    st = "✅ yoqilgan" if site.enabled() else "❌ o'chirilgan"
    await m.answer(f"🌐 Sayt kuzatuvi: {st}\nSayt: {site.SITE_URL}\n"
                   f"Oxirgi ko'rilgan maqola: {db.kv_get('site_last_id') or '—'}\n\n"
                   "Yangi maqola chiqsa, tayyor rasm sizga keladi; kanalga faqat «📤 Kanalga yuborish» "
                   "bosilganda chiqadi.\nO'chirish: /sayt off · Yoqish: /sayt on")


@admin.message(Command("admin"))
async def a_help(m: Message):
    await m.answer(
        "<b>Admin buyruqlari</b>\n"
        "⚙️ Admin panel — menyudagi tugma (rang, kanallar, statistika)\n"
        "📰 Yangilik rasmi — rasm yuboring, izohiga sarlavha yozing (urg'u: *so'z*, teg: [Tezkor])\n"
        "/stat — statistika\n"
        "/sayt — saytdagi yangi maqolalarni kuzatish (on/off)\n"
        "/meta — Facebook va Instagram ulanishini tekshirish\n"
        "/tekshir namangan 2026-10-07 — vaqt va manbasini ko'rish\n"
        "/oylik 2026-11 + jadval — yangi oy vaqtlarini kiritish\n"
        "/vaqt namangan 2026-10-07 04:58 06:16 12:35 16:01 17:50 19:04 — vaqtni qo'lda kiritish\n"
        "/sinov — kechki xabar va rasmni o'zingizga yuborish\n"
        "/hozir_yubor — kechki yuborishni hozir hammaga ishga tushirish\n"
        "/xabar — (reply qilib) hammaga reklama/e'lon"
    )
