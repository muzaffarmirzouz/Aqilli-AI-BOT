import asyncio
import calendar
import logging
import re
from datetime import date, timedelta

from aiogram import Bot, F, Router
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (BufferedInputFile, CallbackQuery, ChatMemberUpdated,
                           KeyboardButton, Message, ReplyKeyboardMarkup)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from . import db, jobs, prayer_table, sources, texts
from .config import ADMIN_IDS, PRAYER_KEYS, SEND_AT
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


def menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=B_PRAYER)],
            [KeyboardButton(text=B_WEATHER), KeyboardButton(text=B_RATES)],
            [KeyboardButton(text=B_REGION), KeyboardButton(text=B_NOTIFY)] if MULTI
            else [KeyboardButton(text=B_NOTIFY)],
            [KeyboardButton(text=B_CHANNEL)],
        ],
        resize_keyboard=True,
    )


def region_kb(prefix: str):
    kb = InlineKeyboardBuilder()
    for k, v in REGIONS.items():
        kb.button(text=v[0], callback_data=f"{prefix}:{k}")
    kb.adjust(3)
    return kb.as_markup()


def user_region(uid: int) -> str:
    u = db.get_user(uid)
    return (u["region"] if u and u["region"] in REGIONS else None) or DEFAULT_REGION


def today() -> date:
    return jobs.now().date()


# =================== /start va hudud ===================
@pm.message(CommandStart())
async def start(m: Message):
    db.upsert_user(m.from_user.id, m.from_user.full_name)
    u = db.get_user(m.from_user.id)
    if not u["region"] and not MULTI:
        db.set_user(m.from_user.id, region=DEFAULT_REGION)
        await m.answer(
            f"Assalomu alaykum, {m.from_user.first_name}! 👋\n\n"
            f"Men {region_name(DEFAULT_REGION)} vaqti bilan namoz vaqtlari, ob-havo va valyuta kursini "
            f"ko'rsataman. Har kuni soat {SEND_AT} da ertangi kun ma'lumotlarini yuboraman.\n\n"
            "Kerakli bo'limni tanlang 👇",
            reply_markup=menu(),
        )
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
    await m.answer(f"📍 {region_name(u['region'])} vaqti bilan.\nKerakli bo'limni tanlang 👇",
                   reply_markup=menu())


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
        reply_markup=menu(),
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
    img = await asyncio.to_thread(jobs.poster_bytes, r, d, data, "Namoz vaqtlari", "")
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
    await m.answer(texts.weather_now(r, w, t) + "\n\n" + texts.weather_day(r, t + timedelta(days=1), w))


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
    for c in db.chats_of(m.from_user.id):
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
    kb.button(text="🖼 Hozir sinov post", callback_data=f"cht:{cid}")
    kb.adjust(1)
    return kb.as_markup()


@router.callback_query(F.data.startswith("ch:"))
async def chat_settings(c: CallbackQuery, bot: Bot):
    cid = int(c.data[3:])
    ch = db.get_chat(cid)
    if not ch or not await is_chat_admin(bot, cid, c.from_user.id):
        return await c.answer("Ruxsat yo'q", show_alert=True)
    await c.message.answer(f"⚙️ <b>{ch['title']}</b>\n📍 Hudud: {region_name(ch['region'])}",
                           reply_markup=chat_kb(cid))
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
    """/vaqt namangan 2026-10-07 04:58 06:16 12:01 16:01 17:50 19:04"""
    parts = (command.args or "").split()
    try:
        region, ds, *ts = parts
        assert region in REGIONS and len(ts) == 6
        d = date.fromisoformat(ds)
        times = {k: sources._norm_time(v) for k, v in zip(PRAYER_KEYS, ts)}
        assert sources.valid_times(times)
    except Exception:
        return await m.answer("Format: <code>/vaqt namangan 2026-10-07 04:58 06:16 12:01 16:01 17:50 19:04</code>\n"
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
    """Kechki xabar va kanal rasmini adminning o'ziga yuboradi."""
    await jobs.evening(bot, only_user=m.from_user.id)
    r = user_region(m.from_user.id)
    d = today() + timedelta(days=1)
    data = await jobs.collect(r, d)
    img = await asyncio.to_thread(jobs.poster_bytes, r, d, data, "Namanganliklar.uz", "@namanganliklar")
    if img:
        await m.answer_photo(BufferedInputFile(img, "sinov.jpg"), caption=jobs.channel_caption(r, d, data, None))
    else:
        await m.answer("⚠️ Ertangi vaqtlar topilmadi — rasm chiqmaydi.")


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
        DEFAULT_REGION, date(y, mo, d1), {"times": days[d1], "hijri": ""})
    await m.answer(msg)


@admin.message(Command("admin"))
async def a_help(m: Message):
    await m.answer(
        "<b>Admin buyruqlari</b>\n"
        "/stat — statistika\n"
        "/tekshir namangan 2026-10-07 — vaqt va manbasini ko'rish\n"
        "/oylik 2026-11 + jadval — yangi oy vaqtlarini kiritish\n"
        "/vaqt namangan 2026-10-07 04:58 06:16 12:01 16:01 17:50 19:04 — vaqtni qo'lda kiritish\n"
        "/sinov — kechki xabar va rasmni o'zingizga yuborish\n"
        "/hozir_yubor — kechki yuborishni hozir hammaga ishga tushirish\n"
        "/xabar — (reply qilib) hammaga reklama/e'lon"
    )
