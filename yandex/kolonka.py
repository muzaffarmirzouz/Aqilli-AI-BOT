"""🔊 Yandex Stansiya: namoz vaqtida foydalanuvchining kolonkasi u yozgan matnni aytadi.

Hamma foydalanuvchilar uchun. Har kim o'z Yandex akkauntini QR kod orqali ulaydi,
xohlagan paytda «🔌 Uzish» bilan token va sozlamalarini o'chiradi.
Kirish: /kolonka yoki menyudagi «🔊 Kolonka eslatma».
"""
import asyncio
import html
import io
import logging
import time
from datetime import date, datetime, timedelta

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot import db, sources
from bot.config import ADMIN_IDS, PRAYER_KEYS, TZ
from bot.regions import DEFAULT_REGION, REGIONS

from . import skill
from . import station as ys
from . import store

log = logging.getLogger(__name__)
router = Router()
router.message.filter(F.chat.type == ChatType.PRIVATE)

B_YS = "🔊 Kolonka eslatma"  # bosh menyudagi tugma (bot/handlers.py)

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
DEFAULT_AZAN_CMD = "включи азан"
NO_AZAN = {"quyosh"}  # quyosh chiqishida azon aytilmaydi
LATE_MIN = 5          # bot qayta ishga tushsa, vaqtdan keyin shuncha daqiqagacha baribir aytadi
QR_WAIT_SEC = 180     # QR kod amal qilish vaqti
TEST_COOLDOWN = 15    # «Sinov» tugmasini qayta bosish oralig'i (soniya)
PARALLEL = 15         # bir vaqtda nechta kolonkaga yuboriladi

WAIT: dict[int, str] = {}            # uid -> "cookie" | "text:<namoz>"
_TASKS: set[asyncio.Task] = set()    # fon vazifalariga havola (xotiradan tushib qolmasin)
STATUS: dict = {"tick": None, "started": None, "batches": []}  # eslatuvchi holati (/kolonka_tekshir)


def _bg(coro) -> asyncio.Task:
    t = asyncio.create_task(coro)
    _TASKS.add(t)
    t.add_done_callback(_TASKS.discard)
    return t
QR_TASKS: dict[int, asyncio.Task] = {}
_last_test: dict[int, float] = {}


# ---------------- yordamchilar ----------------
def text_of(p: store.Profile, k: str) -> str:
    return p.texts.get(k) or DEFAULT_TEXT[k]


def enabled(p: store.Profile, k: str) -> bool:
    v = p.enabled.get(k)
    return (k != "quyosh") if v is None else bool(v)


def azan_cmd(p: store.Profile, k: str | None = None) -> str:
    """Foydalanuvchi o'z buyrug'ini yozgan bo'lsa — o'sha; bo'lmasa «Namoz azoni» ko'nikmasi
    (sozlangan bo'lsa, Plus kerak emas) yoki «включи азан» (Yandex Musiqa, Plus kerak)."""
    if p.azan_cmd:
        return p.azan_cmd
    return skill.launch_command(k) if skill.configured() else DEFAULT_AZAN_CMD


def azan_for(p: store.Profile, k: str) -> str | None:
    """Shu namozda matndan keyin Alisa'ga yuboriladigan buyruq (azon) yoki None."""
    return azan_cmd(p, k) if p.azan and k not in NO_AZAN else None


def spoken(p: store.Profile, text: str) -> str:
    return ys.to_cyrillic(text) if p.cyr else text


def now() -> datetime:
    return datetime.now(TZ)


def _at(d: date, hhmm: str) -> datetime:
    h, m = map(int, hhmm.split(":"))
    return datetime(d.year, d.month, d.day, h, m, tzinfo=TZ)


def region_of(uid: int) -> str:
    u = db.get_user(uid)
    return (u["region"] if u and u["region"] in REGIONS else None) or DEFAULT_REGION


async def times_for(region: str, d: date | None = None) -> dict:
    rec = await sources.get_prayer(region, d or now().date())
    return (rec or {}).get("times") or {}


async def next_prayer(p: store.Profile) -> str:
    """«Shom 17:44» — keyingi yoqilgan namoz (bugun yoki ertaga)."""
    n = now()
    for d in (n.date(), n.date() + timedelta(days=1)):
        times = await times_for(region_of(p.uid), d)
        for k in PRAYER_KEYS:
            if enabled(p, k) and times.get(k) and _at(d, times[k]) > n:
                return f"{NAMES[k]} {times[k]}" + ("" if d == n.date() else " (ertaga)")
    return "—"


def _err(e: Exception) -> str:
    return html.escape(str(e) or type(e).__name__)[:400]


# ---------------- matnlar ----------------
INTRO = (
    "🔊 <b>Kolonka eslatma</b>\n\n"
    "Uyingizdagi <b>Yandex Stansiya</b> (Alisa) har namoz vaqtida siz yozgan gapni ovoz chiqarib aytadi — "
    "masalan: «Peshin namozi vaqti kirdi».\n\n"
    "<b>Nima kerak:</b> Yandex Stansiya (Lite, Mini, Midi, Max, Stansiya 2 …) va u ulangan Yandex akkaunt.\n\n"
    "⚠️ <b>Ulashdan oldin o'qing:</b>\n"
    "• Ulash uchun Yandex ilovasida QR kodni skanerlaysiz. Shunda bot Yandex akkauntingizga kirish "
    "<b>tokenini</b> oladi. Bu token bilan nafaqat kolonkani, balki akkauntning boshqa xizmatlarini ham "
    "boshqarish mumkin — bot uni <b>faqat kolonkaga gapirtirish</b> uchun ishlatadi.\n"
    "• Token bot bazasida saqlanadi. Parolingiz botga berilmaydi va bot uni bilmaydi.\n"
    "• Bu Yandex'ning rasmiy xizmati emas: Yandex o'zgartirsa, vaqtincha ishlamay qolishi mumkin.\n"
    "• Xohlagan paytda <b>«🔌 Uzish»</b> tugmasi bilan token va barcha sozlamalaringiz botdan o'chiriladi. "
    "Yandex tomonida ham butunlay chiqish uchun: id.yandex.ru → «Kirish va qurilmalar» → shu kirishni o'chiring.\n\n"
    "Rozimisiz?"
)

LANG_TIP = (
    "💡 Kolonka o'zbekcha yaxshi gapirishi uchun «Дом с Алисой» ilovasida: kolonka ⚙️ → "
    "«Virtual yordamchi» → «Assistent tili» → <b>«O‘zbekcha + Ruscha»</b> ni yoqing."
)


# ---------------- panel ----------------
async def panel_text(p: store.Profile) -> str:
    if not p.connected:
        return INTRO
    times = await times_for(region_of(p.uid))
    acc = f"✅ ulangan ({html.escape(p.login)})" if p.login else "✅ ulangan"
    lines = [
        "🔊 <b>Kolonka eslatma</b>",
        f"Holat: {'✅ yoqilgan' if p.active else '⏸ o‘chiq'}",
        f"Yandex akkaunt: {acc}",
        f"Kolonka: {html.escape(p.device_name or '— (tanlang)')}",
        f"Kirillga o'girish: {'✅' if p.cyr else '❌'}",
        f"🕌 Matndan keyin azon: {'✅ «' + html.escape(azan_cmd(p)) + '»' if p.azan else '❌'}",
        "",
        "<b>Bugun kolonka aytadi:</b>",
    ]
    for k in PRAYER_KEYS:
        mark = "🔔" if enabled(p, k) else "🔕"
        tail = " + 🕌" if azan_for(p, k) else ""
        lines.append(f"{mark} {NAMES[k]} {times.get(k, '--:--')} — «{html.escape(text_of(p, k))}»{tail}")
    if not times:
        lines.append("\n⚠️ Bugungi namoz vaqtlari hali kiritilmagan.")
    if p.active and p.device_id:
        lines.append(f"\n⏰ Keyingi eslatma: {await next_prayer(p)}")
    if not p.device_id:
        lines.append("\n👉 «📻 Kolonkani tanlash» ni bosing.")
    elif not p.active:
        lines.append("\n👉 «🔈 Sinov» bilan tekshirib, «▶️ Yoqish» ni bosing.")
    lines.append("\nNamozni bosib matnini o'zgartirasiz.")
    return "\n".join(lines)


def panel_kb(p: store.Profile):
    kb = InlineKeyboardBuilder()
    if not p.connected:
        kb.button(text="✅ Roziman — QR kod bilan ulash", callback_data="ys:qr")
        kb.adjust(1)
        return kb.as_markup()
    for k in PRAYER_KEYS:
        kb.button(text=f"{'🔔' if enabled(p, k) else '🔕'} {NAMES[k]}", callback_data=f"ys:p:{k}")
    kb.button(text="⏸ O'chirish" if p.active else "▶️ Yoqish", callback_data="ys:on")
    kb.button(text="🔈 Sinov", callback_data="ys:test")
    kb.button(text="📻 Kolonkani tanlash", callback_data="ys:dev")
    kb.button(text=f"🕌 Azon: {'✅' if p.azan else '❌'}", callback_data="ys:az")
    kb.button(text="✏️ Azon buyrug'i", callback_data="ys:azc")
    kb.button(text=f"🔤 Kirill: {'✅' if p.cyr else '❌'}", callback_data="ys:cyr")
    kb.button(text="🔌 Uzish", callback_data="ys:off")
    kb.adjust(3, 3, 2, 2, 2, 1)
    return kb.as_markup()


def prayer_kb(p: store.Profile, k: str):
    kb = InlineKeyboardBuilder()
    kb.button(text="✏️ Matnni o'zgartirish", callback_data=f"ys:e:{k}")
    kb.button(text="🔕 O'chirish" if enabled(p, k) else "🔔 Yoqish", callback_data=f"ys:t:{k}")
    kb.button(text="🔈 Kolonkada eshitish", callback_data=f"ys:s:{k}")
    kb.button(text="↩️ Asl matn", callback_data=f"ys:r:{k}")
    kb.button(text="⬅️ Orqaga", callback_data="ys:back")
    kb.adjust(1, 1, 1, 2)
    return kb.as_markup()


def _profile(uid: int) -> store.Profile:
    if uid in ADMIN_IDS:
        store.migrate_legacy(uid)
    return store.get(uid)


async def show_panel(bot: Bot, uid: int):
    p = _profile(uid)
    await bot.send_message(uid, await panel_text(p), reply_markup=panel_kb(p), disable_web_page_preview=True)


@router.message(Command("kolonka"))
@router.message(F.text == B_YS)
async def cmd(m: Message, bot: Bot):
    WAIT.pop(m.from_user.id, None)
    await show_panel(bot, m.from_user.id)


@router.message(Command("kolonka_tekshir"))
async def cmd_diag(m: Message):
    p = _profile(m.from_user.id)
    if not p.connected:
        return await m.answer("Avval Yandex akkauntini ulang: /kolonka")
    await m.answer("⏳ Yandex tekshirilmoqda…")
    head = [
        f"Holat: {'✅ yoqilgan' if p.active else '⏸ O‘CHIQ — «▶️ Yoqish» ni bosing'}",
        f"Kolonka: {p.device_name or '— tanlanmagan'} · Azon: {'✅' if p.azan else '❌'}",
        f"Keyingi eslatma: {await next_prayer(p) if p.active else '—'}",
        f"Eslatuvchi: ishga tushgan {STATUS['started'] or '—'}, oxirgi tekshiruv {STATUS['tick'] or '—'}",
    ]
    if p.last_err:
        head.append(f"Oxirgi xato ({p.last_err_day}): {p.last_err[:200]}")
    mine = [b for b in STATUS["batches"] if p.uid in b.get("uids", ())][-5:]
    for b in mine:
        head.append(f"• {b['time']} {NAMES[b['k']]}: {b['res'].get(p.uid, '?')}")
    head.append("")
    try:
        lines = head + await ys.diagnose(p)
    except Exception as e:
        lines = head + [f"❌ {type(e).__name__}: {e}"]
    await m.answer("🔍 <b>Kolonka tekshiruvi</b>\n" + html.escape("\n".join(lines))[:3900])


@router.message(Command("kolonka_stat"), F.from_user.id.in_(ADMIN_IDS))
async def cmd_stat(m: Message):
    st = store.stats()
    await m.answer(f"🔊 Kolonka eslatma: {st['connected']} ta akkaunt ulangan, {st['active']} ta yoqilgan.")


@router.message(Command("kolonka_cookie"), F.from_user.id.in_(ADMIN_IDS))
async def cmd_cookie(m: Message):
    """Zaxira: QR ishlamasa, admin brauzer cookie'si bilan ulaydi."""
    WAIT[m.from_user.id] = "cookie"
    await m.answer(COOKIE_HELP, disable_web_page_preview=True)


COOKIE_HELP = (
    "🔑 <b>Cookie bilan ulash (zaxira usul)</b>\n\n"
    "Chrome'da «Get cookies.txt LOCALLY» kengaytmasi bilan passport.yandex.ru sahifasining "
    "cookie'larini eksport qilib, .txt faylni menga yuboring (yoki Network → Request Headers → "
    "Cookie qiymatini matn qilib). Xabaringizni o'chirib qo'yaman.\n\nBekor qilish: /kolonka"
)


# ---------------- tugmalar ----------------
@router.callback_query(F.data.startswith("ys:"))
async def cb(c: CallbackQuery, bot: Bot):
    parts = c.data.split(":")
    act, k = parts[1], (parts[2] if len(parts) > 2 else "")
    uid = c.from_user.id
    p = _profile(uid)

    if act == "qr":
        await c.answer()
        return await start_qr(bot, uid)
    if act == "qrx":
        t = QR_TASKS.pop(uid, None)
        if t:
            t.cancel()
        await c.answer("Bekor qilindi")
        try:
            await c.message.delete()
        except Exception:
            pass
        return
    if not p.connected:
        await c.answer("Avval Yandex akkauntini ulang", show_alert=True)
        return await show_panel(bot, uid)

    if act == "on":
        if not p.active and not p.device_id:
            return await c.answer("Avval kolonkani tanlang", show_alert=True)
        p.active = 0 if p.active else 1
        p.save()
        if p.active:
            await c.answer(f"Yoqildi ✅ Keyingi eslatma: {await next_prayer(p)}", show_alert=True)
        else:
            await c.answer("O'chirildi")
        return await _refresh(c, p)
    if act == "az":
        p.azan = 0 if p.azan else 1
        p.save()
        await c.answer("Azon yoqildi — «🔈 Kolonkada eshitish» bilan sinab ko'ring" if p.azan else "Azon o'chirildi",
                       show_alert=bool(p.azan))
        return await _refresh(c, p)
    if act == "azc":
        WAIT[uid] = "azan"
        await c.answer()
        return await c.message.answer(
            "🕌 <b>Azon uchun Alisa'ga qanday buyruq berilsin?</b>\n\n"
            f"Hozirgi: <code>{html.escape(azan_cmd(p))}</code>\n\n"
            "Bot matnni aytib bo'lgach, Alisa'ga xuddi siz ovoz bilan aytgandek shu buyruqni yuboradi.\n"
            + (f"Standart: botning «{html.escape(skill.SKILL_NAME)}» ko'nikmasi — Yandex Plus'siz azon chaladi.\n"
               if skill.configured() else "Standart «включи азан» Yandex Musiqa'dan chaladi (Yandex Plus kerak).\n")
            + "Boshqa variant: <code>включи азан мишари рашид</code> (Plus bilan).\n"
            "Asl holiga qaytarish: <code>-</code> · Bekor qilish: /kolonka")
    if act == "cyr":
        p.cyr = 0 if p.cyr else 1
        p.save()
        await c.answer()
        return await _refresh(c, p)
    if act == "back":
        await c.answer()
        return await _refresh(c, p)
    if act == "dev":
        await c.answer("Kolonkalar olinmoqda…")
        return await choose_device(bot, p)
    if act == "d":
        name = next((b.text for row in c.message.reply_markup.inline_keyboard for b in row
                     if b.callback_data == c.data), "Kolonka")
        p.device_id, p.device_name, p.mode = k, name, ""
        p.save()
        await c.answer("Tanlandi")
        return await c.message.edit_text(
            f"✅ Kolonka tanlandi: <b>{html.escape(name)}</b>\n\n"
            "«🔈 Sinov» bilan tekshirib, ishlasa «▶️ Yoqish» ni bosing.\n\n" + LANG_TIP,
            reply_markup=panel_kb(p))
    if act in ("test", "s"):
        if not p.device_id:
            return await c.answer("Avval kolonkani tanlang", show_alert=True)
        left = TEST_COOLDOWN - (time.time() - _last_test.get(uid, 0))
        if left > 0:
            return await c.answer(f"{int(left) + 1} soniyadan keyin qayta bosing", show_alert=True)
        _last_test[uid] = time.time()
        await c.answer("Kolonkaga yuborilmoqda…")
        if act == "s":
            return await speak(bot, p, text_of(p, k), azan_for(p, k))
        return await speak(bot, p, "Assalomu alaykum. Kolonka namoz vaqtlarini eslatishga tayyor.")
    if act == "off":
        kb = InlineKeyboardBuilder()
        kb.button(text="🔌 Ha, uzish", callback_data="ys:off2")
        kb.button(text="⬅️ Yo'q", callback_data="ys:back")
        kb.adjust(2)
        await c.answer()
        return await c.message.edit_text(
            "🔌 <b>Yandex akkauntini uzasizmi?</b>\n\nToken, tanlangan kolonka va barcha matnlaringiz "
            "botdan butunlay o'chiriladi, eslatmalar to'xtaydi.", reply_markup=kb.as_markup())
    if act == "off2":
        await ys.reset(uid)
        store.delete(uid)
        WAIT.pop(uid, None)
        await c.answer("Uzildi")
        return await c.message.edit_text(
            "✅ Yandex akkauntingiz botdan uzildi — token va sozlamalar o'chirildi.\n\n"
            "Yandex tomonida ham chiqish uchun: <b>id.yandex.ru</b> → «Kirish va qurilmalar» "
            "(«Входы и устройства») → shu kirishni o'chiring.\n\nQayta ulash: /kolonka",
            disable_web_page_preview=True)
    if act == "p":
        await c.answer()
        return await show_prayer(c, p, k)
    if act == "e":
        WAIT[uid] = f"text:{k}"
        await c.answer()
        return await c.message.answer(
            f"✏️ <b>{NAMES[k]}</b> vaqtida kolonka nima desin? Matnni yozing "
            f"({ys.MAX_PHRASE} belgigacha).\n\n"
            "Masalan: <code>Peshin namozi vaqti kirdi, namozga shoshiling</code>\n"
            "Bekor qilish: /kolonka")
    if act == "t":
        p.enabled[k] = not enabled(p, k)
        p.save()
        await c.answer()
        return await show_prayer(c, p, k)
    if act == "r":
        p.texts.pop(k, None)
        p.save()
        await c.answer("Asl matn tiklandi")
        return await show_prayer(c, p, k)
    await c.answer()


async def show_prayer(c: CallbackQuery, p: store.Profile, k: str):
    times = await times_for(region_of(p.uid))
    try:
        await c.message.edit_text(
            f"🕌 <b>{NAMES[k]}</b> (bugun {times.get(k, '--:--')})\n"
            f"Holat: {'🔔 yoqilgan' if enabled(p, k) else '🔕 o‘chiq'}\n\n"
            f"Kolonka aytadi: «{html.escape(spoken(p, text_of(p, k)))}»"
            + (f"\nKeyin azon: «{html.escape(azan_cmd(p))}»" if azan_for(p, k) else ""),
            reply_markup=prayer_kb(p, k))
    except Exception:
        pass  # "message is not modified"


async def _refresh(c: CallbackQuery, p: store.Profile):
    try:
        await c.message.edit_text(await panel_text(p), reply_markup=panel_kb(p), disable_web_page_preview=True)
    except Exception:
        pass


async def speak(bot: Bot, p: store.Profile, text: str, then_cmd: str | None = None):
    try:
        await ys.say(p, text, then_cmd)
        extra = f"\n🕌 Keyin Alisa'ga: «{html.escape(then_cmd)}»" if then_cmd else ""
        await bot.send_message(p.uid, f"🔊 Kolonkaga yuborildi: «{html.escape(spoken(p, text))}»{extra}")
    except ys.YandexAuthError as e:
        await bot.send_message(p.uid, f"❌ {_err(e)}\nQayta ulash: /kolonka → «🔌 Uzish», keyin qaytadan ulang.")
    except Exception as e:
        log.warning("kolonka sinov uid=%s: %s", p.uid, e)
        await bot.send_message(p.uid, f"❌ {_err(e)}")


async def choose_device(bot: Bot, p: store.Profile) -> bool:
    try:
        devs = await ys.speakers(p)
    except Exception as e:
        await bot.send_message(p.uid, f"❌ {_err(e)}")
        return False
    if not devs:
        await bot.send_message(p.uid, "⚠️ Akkauntingizda Yandex kolonka topilmadi. Kolonka aynan shu "
                                      "akkauntga ulanganini «Дом с Алисой» ilovasida tekshiring.")
        return True
    kb = InlineKeyboardBuilder()
    for d in devs:
        kb.button(text=d["name"], callback_data=f"ys:d:{d['id']}"[:64])
    kb.adjust(1)
    await bot.send_message(p.uid, "📻 Qaysi kolonka gapirsin?", reply_markup=kb.as_markup())
    return True


# ---------------- QR orqali ulash ----------------
def qr_png(data: str) -> bytes | None:
    try:
        import qrcode
    except ImportError:
        return None
    img = qrcode.make(data, box_size=10, border=3)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue()


async def start_qr(bot: Bot, uid: int):
    old = QR_TASKS.pop(uid, None)
    if old:
        old.cancel()
    qr = ys.QrLogin()
    try:
        link = await qr.start()
    except Exception as e:
        await qr.close()
        log.warning("QR start uid=%s: %s", uid, e)
        return await bot.send_message(uid, f"❌ QR kod olinmadi: {_err(e)}\nBirozdan keyin qayta urinib ko'ring.")
    kb = InlineKeyboardBuilder()
    kb.button(text="📱 Telefonda ochish", url=link)
    kb.button(text="❌ Bekor qilish", callback_data="ys:qrx")
    kb.adjust(1)
    caption = (
        "📷 <b>Yandex akkauntini ulash</b>\n\n"
        "<b>Boshqa qurilmadan</b> (kompyuter/planshet) ko'rayotgan bo'lsangiz: telefonda Yandex ilovasini "
        "(yoki «Дом с Алисой») oching → kamera/QR skaner bilan shu kodni skanerlang → «Kirish»ni tasdiqlang.\n\n"
        "<b>Shu telefondan</b> ko'rayotgan bo'lsangiz: «📱 Telefonda ochish» ni bosing va Yandex'da tasdiqlang.\n\n"
        f"⏳ Kod {QR_WAIT_SEC // 60} daqiqa amal qiladi. Tasdiqlaganingizdan keyin bot o'zi xabar beradi."
    )
    png = qr_png(link)
    if png:
        msg = await bot.send_photo(uid, BufferedInputFile(png, "yandex_qr.png"), caption=caption,
                                   reply_markup=kb.as_markup())
    else:
        msg = await bot.send_message(uid, caption, reply_markup=kb.as_markup())
    QR_TASKS[uid] = _bg(_wait_qr(bot, uid, qr, msg.message_id))


async def _wait_qr(bot: Bot, uid: int, qr: ys.QrLogin, msg_id: int):
    result = None
    try:
        deadline = time.time() + QR_WAIT_SEC
        while time.time() < deadline:
            await asyncio.sleep(3)
            try:
                result = await qr.check()
            except ys.YandexError as e:
                await bot.send_message(uid, f"❌ {_err(e)}")
                return
            except Exception as e:
                log.warning("QR check uid=%s: %s", uid, e)
                continue
            if result:
                break
    except asyncio.CancelledError:
        return
    finally:
        await qr.close()
        if QR_TASKS.get(uid) is asyncio.current_task():
            QR_TASKS.pop(uid, None)
        try:
            await bot.delete_message(uid, msg_id)
        except Exception:
            pass
    if not result:
        kb = InlineKeyboardBuilder()
        kb.button(text="🔄 Yangi QR kod", callback_data="ys:qr")
        return await bot.send_message(uid, "⌛ QR kod muddati tugadi.", reply_markup=kb.as_markup())
    x_token, login = result
    await _connected(bot, uid, x_token, login)


async def _connected(bot: Bot, uid: int, x_token: str, login: str):
    await ys.reset(uid)
    p = store.get(uid)
    p.x_token, p.login = x_token, login
    p.mode = p.scenario = p.csrf_url = ""
    p.save()
    await bot.send_message(uid, f"✅ Yandex akkaunti ulandi{': <b>' + html.escape(login) + '</b>' if login else ''}.\n"
                                "Kolonkalar ro'yxati olinmoqda…")
    await choose_device(bot, p)


# ---------------- matn kiritish ----------------
@router.message(F.func(lambda m: WAIT.get(m.from_user.id) is not None
                       and not (m.text or "").startswith("/")))
async def receive(m: Message, bot: Bot):
    uid = m.from_user.id
    state = WAIT[uid]

    if state == "cookie":
        raw = m.text or ""
        if m.document:
            if (m.document.file_size or 0) > 2_000_000:
                return await m.answer("Fayl juda katta.")
            raw = (await bot.download(m.document)).read().decode("utf-8", "ignore")
        try:
            await m.delete()
        except Exception:
            pass
        if "Session_id" not in raw:
            return await m.answer("⚠️ Bu matnda <code>Session_id</code> yo'q. Bekor qilish: /kolonka")
        try:
            x_token, login = await ys.cookies_to_token(raw)
        except Exception as e:
            return await m.answer(f"❌ {_err(e)}")
        WAIT.pop(uid, None)
        return await _connected(bot, uid, x_token, login)

    if state == "azan":
        p = store.get(uid)
        txt = " ".join((m.text or "").split())
        if not txt:
            return await m.answer("Buyruq yozing. Bekor qilish: /kolonka")
        if len(txt) > ys.MAX_PHRASE:
            return await m.answer(f"Juda uzun. {ys.MAX_PHRASE} belgigacha yozing.")
        p.azan_cmd = "" if txt == "-" else txt
        p.save()
        WAIT.pop(uid, None)
        return await m.answer(f"✅ Azon buyrug'i: «{html.escape(azan_cmd(p))}»\n"
                              "Namozni bosib «🔈 Kolonkada eshitish» bilan sinab ko'ring.",
                              reply_markup=panel_kb(p))

    if state.startswith("text:"):
        k = state.split(":", 1)[1]
        p = store.get(uid)
        txt = " ".join((m.text or "").split())
        if not txt:
            return await m.answer("Matn yozing. Bekor qilish: /kolonka")
        if len(spoken(p, txt)) > ys.MAX_PHRASE:
            return await m.answer(f"Juda uzun ({len(spoken(p, txt))} belgi). {ys.MAX_PHRASE} belgigacha yozing.")
        p.texts[k] = txt
        p.save()
        WAIT.pop(uid, None)
        await m.answer(f"✅ {NAMES[k]} matni saqlandi.\nKolonka aytadi: «{html.escape(spoken(p, txt))}»",
                       reply_markup=prayer_kb(p, k))


# ---------------- har kungi yuboruvchi ----------------
async def _send_one(bot: Bot, p: store.Profile, k: str, sem: asyncio.Semaphore, res: dict):
    async with sem:
        p = store.get(p.uid)  # eng yangi sozlamalar (foydalanuvchi shu daqiqada o'zgartirgan bo'lishi mumkin)
        if not (p.active and p.ready and enabled(p, k)):
            res[p.uid] = "o'tkazildi (o'chiq)"
            return False
        try:
            await ys.say(p, text_of(p, k), azan_for(p, k))
            res[p.uid] = "✅ yuborildi"
            return True
        except ys.YandexAuthError:
            res[p.uid] = "❌ token bekor — o'chirildi"
            p.active = 0
            p.save()
            try:
                await bot.send_message(p.uid, "🔊❌ Yandex akkauntingiz uzilib qolgan (token bekor qilingan). "
                                              "Kolonka eslatmalari to'xtatildi.\nQayta ulash: /kolonka → «🔌 Uzish», "
                                              "keyin QR kod bilan qaytadan ulang.")
            except Exception:
                pass
        except Exception as e:
            res[p.uid] = f"❌ {str(e)[:150]}"
            log.warning("kolonka uid=%s %s: %s", p.uid, k, e)
            today = now().date().isoformat()
            if p.last_err_day != today:  # kuniga bir martadan ko'p bezovta qilmaymiz
                p.last_err_day, p.last_err = today, str(e)[:300]
                p.save()
                try:
                    await bot.send_message(p.uid, f"🔊❌ {NAMES[k]} eslatmasi kolonkaga yetib bormadi: {_err(e)}")
                except Exception:
                    pass
        return False


async def _batch(bot: Bot, k: str, profiles: list[store.Profile]):
    sem = asyncio.Semaphore(PARALLEL)
    res: dict[int, str] = {}
    rec = {"k": k, "time": now().strftime("%d.%m %H:%M:%S"), "uids": {p.uid for p in profiles}, "res": res}
    STATUS["batches"] = (STATUS["batches"] + [rec])[-30:]
    out = await asyncio.gather(*(_send_one(bot, p, k, sem, res) for p in profiles), return_exceptions=True)
    log.info("kolonka %s: %d/%d yuborildi", k, sum(1 for r in out if r is True), len(profiles))


async def scheduler(bot: Bot):
    """Har namoz vaqtida yoqilgan barcha foydalanuvchilarning kolonkasiga yuboradi."""
    _bg(skill.serve())  # «Namoz azoni» ko'nikmasi webhook'i (PORT bo'lsa)
    STATUS["started"] = now().strftime("%d.%m %H:%M")
    if len(ADMIN_IDS) == 1:  # eski (bitta admin) sozlamalarini ko'chirish
        store.migrate_legacy(next(iter(ADMIN_IDS)))
    while True:
        wait = 30.0
        try:
            n = now()
            today = n.date()
            STATUS["tick"] = n.strftime("%H:%M:%S")
            by_region: dict[str, list[store.Profile]] = {}
            for p in store.active_profiles():
                by_region.setdefault(region_of(p.uid), []).append(p)
            for region, profs in by_region.items():
                times = await times_for(region, today)
                for k in PRAYER_KEYS:
                    if not times.get(k):
                        continue
                    t = _at(today, times[k])
                    if t <= n < t + timedelta(minutes=LATE_MIN):
                        flag = f"ys_fired_{region}_{k}"
                        if db.kv_get(flag) == today.isoformat():
                            continue
                        db.kv_set(flag, today.isoformat())
                        targets = [p for p in profs if enabled(p, k)]
                        if targets:
                            _bg(_batch(bot, k, targets))
                    elif t > n:
                        wait = min(wait, (t - n).total_seconds())
        except Exception as e:
            log.exception("kolonka scheduler: %s", e)
        await asyncio.sleep(max(1.0, wait))
