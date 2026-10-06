"""Har kungi kechki yuborish va oldindan tekshiruv."""
import asyncio
import logging
from datetime import date, datetime, timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import BufferedInputFile

from . import db, prayer_table, sources, texts
from .config import ADMIN_IDS, PRECHECK_MIN, REMIND_DAYS, SEND_AT, TZ
from .poster import render
from .regions import name as region_name

log = logging.getLogger(__name__)


def now() -> datetime:
    return datetime.now(TZ)


async def collect(region: str, d: date) -> dict:
    """Bir hudud uchun d sanadagi barcha ma'lumot."""
    rec = await sources.get_prayer(region, d)
    try:
        w = await sources.get_weather(region)
    except Exception as e:
        log.warning("ob-havo %s: %s", region, e)
        w = None
    rates = await sources.get_rates(d)
    usd_date = rates.get("USD", {}).get("date", "")
    rates_for_d = usd_date == d.strftime("%d.%m.%Y")
    return {"rec": rec, "w": w, "rates": rates, "rates_for_d": rates_for_d}


def poster_bytes(region: str, d: date, data: dict, brand: str, footer: str) -> bytes | None:
    rec = data["rec"]
    if not rec or not sources.valid_times(rec["times"]):
        return None
    wd = (data["w"] or {}).get("days", {}).get(d.isoformat())
    wtext = ""
    if wd:
        wtext = sources.wmo(wd["code"])[1]
        if wd.get("rain") is not None:
            wtext += f" · yog'in {wd['rain']}%"
    return render(
        d=d, city=region_name(region), times=rec["times"], hijri=rec.get("hijri", ""),
        brand=brand, footer=footer, weather=wd, usd=data["rates"].get("USD"), weather_text=wtext,
    )


def channel_caption(region: str, d: date, data: dict, username: str | None) -> str:
    wd = (data["w"] or {}).get("days", {}).get(d.isoformat())
    lines = [f"🕌 <b>{texts.dstr(d).capitalize()} — {region_name(region)} namoz vaqtlari</b>"]
    if wd:
        ic, t = sources.wmo(wd["code"])
        lines.append(f"{ic} Ob-havo: {wd['tmin']:+d}° … {wd['tmax']:+d}°, {t.lower()}")
    usd = data["rates"].get("USD")
    if usd:
        lines.append(f"💵 1 USD = {texts.money(usd['rate'])} so'm (MB, {usd['date']})")
    if username:
        lines.append(f"\n👉 @{username}")
    return "\n".join(lines)


async def _safe(coro_factory, on_forbidden=None):
    for _ in range(3):
        try:
            return await coro_factory()
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
        except TelegramForbiddenError:
            if on_forbidden:
                on_forbidden()
            return None
        except TelegramBadRequest as e:
            log.warning("bad request: %s", e)
            if "chat not found" in str(e).lower() and on_forbidden:
                on_forbidden()
            return None
        except Exception as e:
            log.warning("yuborishda xato: %s", e)
            await asyncio.sleep(2)
    return None


async def notify_admins(bot: Bot, text: str):
    for a in ADMIN_IDS:
        await _safe(lambda a=a: bot.send_message(a, text))


async def precheck(bot: Bot):
    """Yuborishdan oldin: ertangi vaqtlar bormi va jadval tugashiga necha kun qolgan."""
    today = now().date()
    d = today + timedelta(days=1)
    bad = []
    for r in sorted(db.used_regions()):
        rec = await sources.get_prayer(r, d)
        if not rec or not sources.valid_times(rec["times"]):
            bad.append(region_name(r))
    if bad:
        await notify_admins(
            bot,
            f"🚨 Ertaga ({d.strftime('%d.%m.%Y')}) uchun namoz vaqtlari yo'q: {', '.join(bad)}.\n"
            "Bugun kechqurun kanallarga rasm CHIQMAYDI. Yangi oy jadvalini /oylik bilan kiriting.",
        )

    # Oy (jadval) tugashi haqida eslatma
    for r in sorted(db.used_regions()):
        last = prayer_table.last_date(r)
        if not last:
            continue
        left = (last - today).days
        if 0 <= left <= REMIND_DAYS:
            nxt = last + timedelta(days=1)
            await notify_admins(
                bot,
                f"📅 <b>Namoz vaqtlarini yangilang!</b>\n"
                f"{region_name(r)} jadvali {last.strftime('%d.%m.%Y')} gacha kiritilgan "
                f"({'bugun oxirgi kun' if left == 0 else f'{left} kun qoldi'}).\n\n"
                f"{texts.MONTHS[nxt.month - 1].capitalize()} oyi jadvalini yuboring:\n"
                f"<code>/oylik {nxt.year}-{nxt.month:02d}\n1 bomdod quyosh peshin asr shom xufton\n...</code>\n"
                "(islom.uz jadvalidan nusxa olsangiz ham bo'ladi)",
            )


async def evening(bot: Bot, only_chat: int | None = None, only_user: int | None = None):
    """Ertangi ma'lumotlarni foydalanuvchilarga (matn) va kanallarga (rasm) yuboradi."""
    d = now().date() + timedelta(days=1)
    cache: dict[str, dict] = {}

    async def data_for(region):
        if region not in cache:
            cache[region] = await collect(region, d)
        return cache[region]

    sent_u = sent_c = 0
    missing = set()

    # --- foydalanuvchilar
    users = db.notify_users() if only_chat is None else []
    if only_user:
        u = db.get_user(only_user)
        users = [{"id": only_user, "region": (u["region"] if u and u["region"] else "namangan")}]
    for u in users:
        data = await data_for(u["region"])
        txt = texts.evening_digest(u["region"], d, data["rec"], data["w"], data["rates"], data["rates_for_d"])
        ok = await _safe(lambda: bot.send_message(u["id"], txt),
                         on_forbidden=lambda uid=u["id"]: db.set_user(uid, active=0))
        sent_u += bool(ok)
        await asyncio.sleep(0.04)

    # --- kanallar / guruhlar
    chats = db.active_chats() if only_user is None else []
    if only_chat:
        c = db.get_chat(only_chat)
        chats = [c] if c else []
    for c in chats:
        data = await data_for(c["region"])
        img = await asyncio.to_thread(
            poster_bytes, c["region"], d, data, c["title"] or "",
            f"@{c['username']}" if c["username"] else "",
        )
        if img is None:
            missing.add(region_name(c["region"]))
            continue
        cap = channel_caption(c["region"], d, data, c["username"])
        ok = await _safe(
            lambda: bot.send_photo(c["id"], BufferedInputFile(img, f"namoz_{d.isoformat()}.jpg"), caption=cap),
            on_forbidden=lambda cid=c["id"]: db.set_chat(cid, active=0),
        )
        sent_c += bool(ok)
        await asyncio.sleep(0.1)

    if only_chat is None and only_user is None:
        msg = f"✅ Kechki yuborish: {sent_u} foydalanuvchi, {sent_c} kanal/guruh."
        if missing:
            msg += f"\n⚠️ Vaqt topilmagani uchun rasm chiqmadi: {', '.join(sorted(missing))}"
        await notify_admins(bot, msg)
    return sent_u, sent_c


def _at(day: date, hhmm: str) -> datetime:
    h, m = map(int, hhmm.split(":"))
    return datetime(day.year, day.month, day.day, h, m, tzinfo=TZ)


async def scheduler(bot: Bot):
    """Har kuni SEND_AT da yuboradi, PRECHECK_MIN daqiqa oldin tekshiradi."""
    while True:
        n = now()
        today = n.date()
        send_t = _at(today, SEND_AT)
        check_t = send_t - timedelta(minutes=PRECHECK_MIN)
        last = db.kv_get("last_sent")
        last_chk = db.kv_get("last_check")

        if n >= check_t and n < send_t and last_chk != today.isoformat():
            db.kv_set("last_check", today.isoformat())
            try:
                await precheck(bot)
            except Exception as e:
                log.exception("precheck: %s", e)
            continue

        # Yuborish vaqti keldi (yoki bot qayta ishga tushgan va bugun hali yuborilmagan, 23:30 gacha)
        if send_t <= n < _at(today, "23:30") and last != today.isoformat():
            db.kv_set("last_sent", today.isoformat())
            try:
                await evening(bot)
            except Exception as e:
                log.exception("evening: %s", e)
                await notify_admins(bot, f"❌ Kechki yuborishda xato: {e}")
            continue

        # keyingi hodisagacha kutamiz (ko'pi bilan 60 soniya)
        targets = [t for t in (check_t, send_t) if t > n]
        wait = min([(t - n).total_seconds() for t in targets] + [60])
        await asyncio.sleep(max(1, wait))
