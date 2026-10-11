"""🕌 Namoz vaqtida Telegram xabari: 5 vaqt namoz kirganda foydalanuvchiga xabar (xohlasa — azon audiosi bilan).

Foydalanuvchi o'zi yoqadi: «🕌 Namoz vaqtlari» → «🕌 Namoz vaqtida xabar» yoki /eslatma.
Vaqtlar botdagi jadvaldan (prayer_data/*.csv, /oylik) olinadi — kolonka va poster bilan bir xil.
"""
import asyncio
import logging
import os
from datetime import date, datetime, timedelta

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.types import CallbackQuery, FSInputFile, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from . import db, sources, subscribe
from .config import ADMIN_IDS, TAKBIR, TZ
from .regions import DEFAULT_REGION, REGIONS
from .regions import name as region_name
from .texts import P_ICONS, P_NAMES

log = logging.getLogger(__name__)
router = Router()
router.message.filter(F.chat.type == ChatType.PRIVATE)

B_PN = "🕌 Namoz vaqtida xabar"           # bosh menyudagi tugma (bot/handlers.py)
FIVE = ["bomdod", "peshin", "asr", "shom", "xufton"]   # 5 vaqt (quyosh — namoz emas)
ORDER = ["bomdod", "quyosh", "peshin", "asr", "shom", "xufton"]
AZAN_FILE = os.getenv("AZAN_FILE", "assets/azon.mp3")
LATE_MIN = 5

# users jadvaliga yangi ustunlar
for _col in ("pn INTEGER DEFAULT 0", "pn_audio INTEGER DEFAULT 0"):
    try:
        db._con.execute(f"ALTER TABLE users ADD COLUMN {_col}")
        db._con.commit()
    except Exception:
        pass

_TASKS: set = set()
_audio_id: dict[str, str] = {}   # Telegram'ga bir marta yuklangan azon (file_id)


def now() -> datetime:
    return datetime.now(TZ)


def _at(d: date, hhmm: str) -> datetime:
    h, m = map(int, hhmm.split(":"))
    return datetime(d.year, d.month, d.day, h, m, tzinfo=TZ)


def _user(uid: int):
    return db._con.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()


def _set(uid: int, **kw):
    for k, v in kw.items():
        db._con.execute(f"UPDATE users SET {k}=? WHERE id=?", (v, uid))
    db._con.commit()


def _region(u) -> str:
    return (u["region"] if u and u["region"] in REGIONS else None) or DEFAULT_REGION


# ---------------- sozlash ----------------
def panel(u) -> tuple[str, object]:
    on, audio = bool(u["pn"]), bool(u["pn_audio"])
    text = (
        "🕌 <b>Namoz vaqtida xabar</b>\n\n"
        "Har 5 vaqt namoz (bomdod, peshin, asr, shom, xufton) kirganda bot sizga xabar yuboradi.\n\n"
        f"Holat: {'✅ yoqilgan' if on else '🔕 o‘chiq'}\n"
        f"Azon audiosi: {'✅ yuboriladi' if audio else '❌ faqat matn'}\n"
        f"Hudud: {region_name(_region(u))}"
    )
    kb = InlineKeyboardBuilder()
    kb.button(text="🔕 O'chirish" if on else "✅ Yoqish", callback_data="pn:on")
    kb.button(text="🔊 Azon: ✅" if audio else "🔊 Azon: ❌", callback_data="pn:au")
    kb.adjust(2)
    return text, kb.as_markup()


@router.message(Command("eslatma"))
@router.message(F.text == B_PN)
async def cmd(m: Message):
    db.upsert_user(m.from_user.id, m.from_user.full_name)
    text, kb = panel(_user(m.from_user.id))
    await m.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("pn:"))
async def cb(c: CallbackQuery):
    uid = c.from_user.id
    db.upsert_user(uid, c.from_user.full_name)
    u = _user(uid)
    act = c.data.split(":")[1]
    if act == "open":  # «🕌 Namoz vaqtlari» ichidagi tugma
        await c.answer()
        text, kb = panel(u)
        return await c.message.answer(text, reply_markup=kb)
    if act == "on":
        _set(uid, pn=0 if u["pn"] else 1)
        await c.answer("Yoqildi ✅" if not u["pn"] else "O'chirildi")
    elif act == "au":
        _set(uid, pn_audio=0 if u["pn_audio"] else 1, **({} if u["pn"] else {"pn": 1}))
        await c.answer("Azon audiosi bilan ✅" if not u["pn_audio"] else "Faqat matn")
    text, kb = panel(_user(uid))
    try:
        await c.message.edit_text(text, reply_markup=kb)
    except Exception:
        pass


@router.message(Command("eslatma_stat"), F.from_user.id.in_(ADMIN_IDS))
async def stat(m: Message):
    q = lambda s: db._con.execute(s).fetchone()[0]
    await m.answer(f"🕌 Namoz vaqtida xabar: {q('SELECT COUNT(*) FROM users WHERE pn=1 AND active=1')} kishi "
                   f"(azon bilan: {q('SELECT COUNT(*) FROM users WHERE pn=1 AND pn_audio=1 AND active=1')})")


# ---------------- yuborish ----------------
def message_text(k: str, times: dict, region: str) -> str:
    i = ORDER.index(k)
    tk = TAKBIR[i] if i < len(TAKBIR) else 0
    lines = [f"{P_ICONS[k]} <b>{P_NAMES[k]} namozi vaqti kirdi</b> — {times[k]}"]
    if tk:
        h, m = map(int, times[k].split(":"))
        t2 = (datetime(2000, 1, 1, h, m) + timedelta(minutes=tk)).strftime("%H:%M")
        lines.append(f"🕋 Takbir: {t2}")
    nxt = next((x for x in FIVE if ORDER.index(x) > i and times.get(x)), None)
    if nxt:
        lines.append(f"⏭ Keyingisi: {P_NAMES[nxt]} {times[nxt]}")
    lines.append(f"📍 {region_name(region)}")
    return "\n".join(lines)


async def _send(bot: Bot, uid: int, text: str, audio: bool) -> bool:
    for _ in range(3):
        try:
            if audio and os.path.exists(AZAN_FILE):
                src = _audio_id.get("azan") or FSInputFile(AZAN_FILE, filename="Azon.mp3")
                msg = await bot.send_audio(uid, src, caption=text, title="Azon", performer="Mishari Alafasy")
                if msg.audio and "azan" not in _audio_id:
                    _audio_id["azan"] = msg.audio.file_id
            else:
                await bot.send_message(uid, text)
            return True
        except Exception as e:
            name = type(e).__name__
            if "RetryAfter" in name:
                await asyncio.sleep(getattr(e, "retry_after", 3) + 1)
                continue
            if "Forbidden" in name or "chat not found" in str(e).lower():
                db.set_user(uid, active=0)  # botni bloklagan
                return False
            log.warning("namoz xabari uid=%s: %s", uid, e)
            return False
    return False


async def _batch(bot: Bot, k: str, region: str, times: dict, users: list):
    text = message_text(k, times, region)
    ok = 0
    for u in users:
        if not await subscribe.is_subscribed(bot, u["id"]):
            continue
        ok += await _send(bot, u["id"], text, bool(u["pn_audio"]))
        await asyncio.sleep(0.05)  # Telegram cheklovi (~20 xabar/s)
    log.info("namoz xabari %s %s: %d/%d", region, k, ok, len(users))


async def scheduler(bot: Bot):
    while True:
        wait = 30.0
        try:
            n = now()
            today = n.date()
            rows = db._con.execute("SELECT id, region, pn_audio FROM users WHERE pn=1 AND active=1").fetchall()
            by_region: dict[str, list] = {}
            for u in rows:
                by_region.setdefault(_region(u), []).append(u)
            for region, users in by_region.items():
                rec = await sources.get_prayer(region, today)
                times = (rec or {}).get("times") or {}
                for k in FIVE:
                    if not times.get(k):
                        continue
                    t = _at(today, times[k])
                    if t <= n < t + timedelta(minutes=LATE_MIN):
                        flag = f"pn_fired_{region}_{k}"
                        if db.kv_get(flag) == today.isoformat():
                            continue
                        db.kv_set(flag, today.isoformat())
                        task = asyncio.create_task(_batch(bot, k, region, times, users))
                        _TASKS.add(task)
                        task.add_done_callback(_TASKS.discard)
                    elif t > n:
                        wait = min(wait, (t - n).total_seconds())
        except Exception as e:
            log.exception("namoz xabari scheduler: %s", e)
        await asyncio.sleep(max(1.0, wait))
