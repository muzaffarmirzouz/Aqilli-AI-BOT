"""🔊 Yandex Stansiya: namoz vaqtida kolonka admin yozgan matnni aytadi.

Faqat adminlar uchun (kolonka adminning o'z uyida). Sozlash: /kolonka
"""
import asyncio
import html
import logging
from datetime import date, datetime, timedelta

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot import db, sources
from bot.config import ADMIN_IDS, PRAYER_KEYS, TZ
from bot.regions import DEFAULT_REGION

from . import station as ys

log = logging.getLogger(__name__)
router = Router()
router.message.filter(F.from_user.id.in_(ADMIN_IDS), F.chat.type == ChatType.PRIVATE)

NAMES = {"bomdod": "Bomdod", "quyosh": "Quyosh", "peshin": "Peshin",
         "asr": "Asr", "shom": "Shom", "xufton": "Xufton"}
DEFAULT_TEXT = {
    "bomdod": "Bomdod namozi vaqti kirdi",
    "quyosh": "Quyosh chiqdi, bomdod vaqti tugadi",
    "peshin": "Peshin namozi vaqti kirdi",
    "asr": "Asr namozi vaqti kirdi",
    "shom": "Shom namozi vaqti kirdi",
    "xufton": "Xufton namozi vaqti kirdi",
}
LATE_MIN = 5  # bot qayta ishga tushsa, vaqtdan keyin shuncha daqiqagacha baribir aytadi

WAIT: dict[int, str] = {}  # uid -> "cookie" | "text:<namoz>"


# ---------------- sozlamalar ----------------
def is_on() -> bool:
    return db.kv_get("ys_on") == "1"


def cyr_on() -> bool:
    return db.kv_get("ys_cyr") != "0"


def text_of(key: str) -> str:
    return db.kv_get(f"ys_text_{key}") or DEFAULT_TEXT[key]


def enabled(key: str) -> bool:
    v = db.kv_get(f"ys_en_{key}")
    return (key != "quyosh") if v is None else v == "1"


def spoken(text: str) -> str:
    return ys.to_cyrillic(text) if cyr_on() else text


def now() -> datetime:
    return datetime.now(TZ)


def _at(d: date, hhmm: str) -> datetime:
    h, m = map(int, hhmm.split(":"))
    return datetime(d.year, d.month, d.day, h, m, tzinfo=TZ)


async def today_times() -> dict:
    rec = await sources.get_prayer(DEFAULT_REGION, now().date())
    return (rec or {}).get("times") or {}


# ---------------- panel ----------------
async def panel_text() -> str:
    times = await today_times()
    acc = "✅ ulangan" if ys.cookie() else "❌ ulanmagan"
    dev = db.kv_get("ys_device_name") or ("—" if not ys.device_id() else "tanlangan")
    lines = [
        "🔊 <b>Yandex Stansiya — namoz eslatmasi</b>",
        f"Holat: {'✅ yoqilgan' if is_on() else '⏸ o‘chiq'}",
        f"Yandex akkaunt: {acc}",
        f"Kolonka: {html.escape(dev)}",
        f"Kirillga o'girish: {'✅' if cyr_on() else '❌'}",
        "",
        "<b>Bugun kolonka aytadi:</b>",
    ]
    for k in PRAYER_KEYS:
        t = times.get(k, "--:--")
        mark = "🔔" if enabled(k) else "🔕"
        lines.append(f"{mark} {NAMES[k]} {t} — «{html.escape(text_of(k))}»")
    if not times:
        lines.append("\n⚠️ Bugungi namoz vaqtlari bazada yo'q — /oylik bilan kiriting.")
    lines.append("\nNamozni bosib matnini o'zgartirasiz.")
    return "\n".join(lines)


def panel_kb():
    kb = InlineKeyboardBuilder()
    for k in PRAYER_KEYS:
        kb.button(text=f"{'🔔' if enabled(k) else '🔕'} {NAMES[k]}", callback_data=f"ys:p:{k}")
    kb.button(text="⏸ O'chirish" if is_on() else "▶️ Yoqish", callback_data="ys:on")
    kb.button(text="🔈 Sinov", callback_data="ys:test")
    kb.button(text="🔑 Akkauntni ulash", callback_data="ys:acc")
    kb.button(text="📻 Kolonkani tanlash", callback_data="ys:dev")
    kb.button(text=f"🔤 Kirill: {'✅' if cyr_on() else '❌'}", callback_data="ys:cyr")
    kb.adjust(3, 3, 2, 2, 1)
    return kb.as_markup()


def prayer_kb(k: str):
    kb = InlineKeyboardBuilder()
    kb.button(text="✏️ Matnni o'zgartirish", callback_data=f"ys:e:{k}")
    kb.button(text="🔕 O'chirish" if enabled(k) else "🔔 Yoqish", callback_data=f"ys:t:{k}")
    kb.button(text="🔈 Kolonkada eshitish", callback_data=f"ys:s:{k}")
    kb.button(text="↩️ Asl matn", callback_data=f"ys:r:{k}")
    kb.button(text="⬅️ Orqaga", callback_data="ys:back")
    kb.adjust(1, 1, 1, 2)
    return kb.as_markup()


async def show_panel(bot: Bot, uid: int):
    await bot.send_message(uid, await panel_text(), reply_markup=panel_kb())


@router.message(Command("kolonka"))
async def cmd(m: Message, bot: Bot):
    WAIT.pop(m.from_user.id, None)
    await show_panel(bot, m.from_user.id)


COOKIE_HELP = (
    "🔑 <b>Yandex akkauntini ulash</b>\n\n"
    "Kompyuterda (Chrome):\n"
    "1. <b>passport.yandex.ru/profile</b> sahifasini oching (yoki ochiladigan istalgan yandex.ru sahifasi) "
    "va kolonka ulangan Yandex akkauntingiz bilan kiring.\n"
    "2. <b>F12</b> (Mac: ⌥⌘I) → <b>Network</b> (Сеть) bo'limi → sahifani yangilang.\n"
    "3. Ro'yxatdagi eng birinchi qatorni bosing → <b>Headers</b> → "
    "<b>Request Headers</b> ichidagi <b>Cookie:</b> qiymatini to'liq nusxa oling.\n"
    "4. Shu matnni menga yuboring. Juda uzun bo'lsa — .txt faylga saqlab, faylni yuboring.\n\n"
    "<b>Osonroq yo'l:</b> Chrome'ga «Get cookies.txt LOCALLY» kengaytmasini o'rnating, "
    "yandex.ru sahifasida uni bosib <b>Export</b> qiling va tushgan .txt faylni menga yuboring.\n\n"
    "🔒 Cookie faqat botingiz bazasida saqlanadi, xabaringizni o'chirib qo'yaman. "
    "Yandex parolingizni o'zgartirsangiz yoki akkauntdan chiqsangiz, qaytadan ulash kerak bo'ladi.\n\n"
    "Bekor qilish: /kolonka"
)


@router.callback_query(F.data.startswith("ys:"))
async def cb(c: CallbackQuery, bot: Bot):
    if c.from_user.id not in ADMIN_IDS:
        return await c.answer("Ruxsat yo'q", show_alert=True)
    parts = c.data.split(":")
    act, k = parts[1], (parts[2] if len(parts) > 2 else "")
    uid = c.from_user.id

    if act == "on":
        if not is_on() and not (ys.cookie() and ys.device_id()):
            return await c.answer("Avval akkauntni ulang va kolonkani tanlang", show_alert=True)
        db.kv_set("ys_on", "0" if is_on() else "1")
        await c.answer("Yoqildi" if is_on() else "O'chirildi")
        return await _refresh(c)
    if act == "cyr":
        db.kv_set("ys_cyr", "0" if cyr_on() else "1")
        await c.answer()
        return await _refresh(c)
    if act == "back":
        await c.answer()
        return await c.message.edit_text(await panel_text(), reply_markup=panel_kb())
    if act == "acc":
        WAIT[uid] = "cookie"
        await c.answer()
        return await c.message.answer(COOKIE_HELP, disable_web_page_preview=True)
    if act == "dev":
        await c.answer("Kolonkalar olinmoqda…")
        return await choose_device(bot, uid)
    if act == "d":
        name = next((b.text for row in c.message.reply_markup.inline_keyboard for b in row
                     if b.callback_data == c.data), "")
        db.kv_set("ys_device", k)
        db.kv_set("ys_device_name", name)
        db.kv_set("ys_scenario", "")
        await c.answer("Tanlandi")
        return await c.message.edit_text(f"✅ Kolonka tanlandi: <b>{html.escape(name)}</b>\n\n"
                                         "«🔈 Sinov» tugmasi bilan tekshirib ko'ring.",
                                         reply_markup=panel_kb())
    if act == "test":
        await c.answer("Yuborilmoqda…")
        return await speak(bot, uid, "Assalomu alaykum. Kolonka namoz vaqtlarini eslatishga tayyor.")
    if act == "p":
        await c.answer()
        return await show_prayer(c, k)
    if act == "e":
        WAIT[uid] = f"text:{k}"
        await c.answer()
        return await c.message.answer(
            f"✏️ <b>{NAMES[k]}</b> vaqtida kolonka nima desin? Matnni yozing "
            f"({ys.MAX_PHRASE} belgigacha).\n\n"
            "Masalan: <code>Peshin namozi vaqti kirdi, namozga shoshiling</code>\n"
            "Lotin yoki kirillda yozsangiz bo'ladi. Bekor qilish: /kolonka")
    if act == "t":
        db.kv_set(f"ys_en_{k}", "0" if enabled(k) else "1")
        await c.answer()
        return await show_prayer(c, k)
    if act == "r":
        db.kv_set(f"ys_text_{k}", "")
        await c.answer("Asl matn tiklandi")
        return await show_prayer(c, k)
    if act == "s":
        await c.answer("Yuborilmoqda…")
        return await speak(bot, uid, text_of(k))
    await c.answer()


async def show_prayer(c: CallbackQuery, k: str):
    times = await today_times()
    try:
        await c.message.edit_text(
            f"🕌 <b>{NAMES[k]}</b> (bugun {times.get(k, '--:--')})\n"
            f"Holat: {'🔔 yoqilgan' if enabled(k) else '🔕 o‘chiq'}\n\n"
            f"Matn: «{html.escape(text_of(k))}»\n"
            f"Kolonka o'qiydi: «{html.escape(spoken(text_of(k)))}»",
            reply_markup=prayer_kb(k))
    except Exception:
        pass  # "message is not modified"


async def _refresh(c: CallbackQuery):
    try:
        await c.message.edit_text(await panel_text(), reply_markup=panel_kb())
    except Exception:
        pass


async def speak(bot: Bot, uid: int, text: str):
    try:
        await ys.say(text, cyrillic=cyr_on())
        await bot.send_message(uid, f"🔊 Kolonkaga yuborildi: «{html.escape(spoken(text))}»")
    except ys.YandexError as e:
        await bot.send_message(uid, f"❌ {html.escape(str(e))}")
    except Exception as e:
        log.exception("kolonka")
        await bot.send_message(uid, f"❌ Xato: {html.escape(str(e))[:300]}")


async def choose_device(bot: Bot, uid: int, raw_cookie: str | None = None) -> bool:
    try:
        async with ys.Client(raw_cookie) as cl:
            devs = await cl.speakers()
    except ys.YandexError as e:
        await bot.send_message(uid, f"❌ {html.escape(str(e))}")
        return False
    except Exception as e:
        log.exception("kolonkalar")
        await bot.send_message(uid, f"❌ Yandex bilan bog'lanib bo'lmadi: {html.escape(str(e))[:300]}")
        return False
    if not devs:
        await bot.send_message(uid, "⚠️ Akkauntda birorta ham Yandex kolonka topilmadi. "
                                    "Kolonka shu akkauntga ulanganini «Дом с Алисой» ilovasida tekshiring.")
        return True
    kb = InlineKeyboardBuilder()
    for d in devs:
        kb.button(text=d["name"], callback_data=f"ys:d:{d['id']}"[:64])
    kb.adjust(1)
    await bot.send_message(uid, "📻 Qaysi kolonka gapirsin?", reply_markup=kb.as_markup())
    return True


@router.message(F.func(lambda m: WAIT.get(m.from_user.id) is not None
                       and not (m.text or "").startswith("/")))
async def receive(m: Message, bot: Bot):
    uid = m.from_user.id
    state = WAIT[uid]

    if state == "cookie":
        raw = m.text or ""
        if m.document:
            if (m.document.file_size or 0) > 2_000_000:
                return await m.answer("Fayl juda katta. Faqat yandex.ru sahifasi cookie'larini eksport qiling.")
            raw = (await bot.download(m.document)).read().decode("utf-8", "ignore")
        try:
            await m.delete()  # cookie chatda qolmasin
        except Exception:
            pass
        if "Session_id" not in raw:
            return await m.answer("⚠️ Bu matnda <code>Session_id</code> yo'q — Cookie to'liq nusxalanmagan "
                                  "yoki Yandex'ga kirilmagan. Qaytadan urinib ko'ring. Bekor qilish: /kolonka")
        await m.answer("⏳ Tekshirilmoqda…")
        if await choose_device(bot, uid, raw):
            ys.set_cookie(raw)
            WAIT.pop(uid, None)
            await m.answer("✅ Yandex akkaunti ulandi. Endi kolonkani tanlang.")
        return

    if state.startswith("text:"):
        k = state.split(":", 1)[1]
        txt = " ".join((m.text or "").split())
        if not txt:
            return await m.answer("Matn yozing. Bekor qilish: /kolonka")
        if len(spoken(txt)) > ys.MAX_PHRASE:
            return await m.answer(f"Juda uzun ({len(spoken(txt))} belgi). {ys.MAX_PHRASE} belgigacha yozing.")
        db.kv_set(f"ys_text_{k}", txt)
        WAIT.pop(uid, None)
        await m.answer(f"✅ {NAMES[k]} matni saqlandi.\nKolonka o'qiydi: «{html.escape(spoken(txt))}»",
                       reply_markup=prayer_kb(k))


# ---------------- har kungi ishlovchi ----------------
async def _notify(bot: Bot, text: str):
    for a in ADMIN_IDS:
        try:
            await bot.send_message(a, text)
        except Exception:
            pass


async def scheduler(bot: Bot):
    """Har namoz vaqtida (yoqilgan bo'lsa) kolonkaga matnni yuboradi."""
    while True:
        wait = 30.0
        try:
            if is_on() and ys.cookie() and ys.device_id():
                n = now()
                today = n.date()
                times = await today_times()
                for k in PRAYER_KEYS:
                    if not enabled(k) or not times.get(k):
                        continue
                    t = _at(today, times[k])
                    if t <= n < t + timedelta(minutes=LATE_MIN):
                        if db.kv_get(f"ys_last_{k}") == today.isoformat():
                            continue
                        db.kv_set(f"ys_last_{k}", today.isoformat())
                        try:
                            await ys.say(text_of(k), cyrillic=cyr_on())
                            log.info("kolonka: %s %s aytildi", k, times[k])
                        except Exception as e:
                            log.warning("kolonka %s: %s", k, e)
                            await _notify(bot, f"🔊❌ {NAMES[k]} eslatmasi kolonkaga yuborilmadi: "
                                               f"{html.escape(str(e))[:300]}")
                    elif t > n:
                        wait = min(wait, (t - n).total_seconds())
        except Exception as e:
            log.exception("kolonka scheduler: %s", e)
        await asyncio.sleep(max(1.0, wait))
