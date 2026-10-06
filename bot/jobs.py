"""Har kungi kechki yuborish va oldindan tekshiruv."""
import asyncio
import logging
from datetime import date, datetime, timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import BufferedInputFile, InputMediaPhoto

from . import db, prayer_table, sources, texts
from .config import AD_CONTACT, ADMIN_IDS, BRAND, PRECHECK_MIN, REMIND_DAYS, SEND_AT, TZ
from .poster import default_logo, render, render_rates, render_weather
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


def weather_bytes(region: str, d: date, data: dict, **style) -> bytes | None:
    days = (data["w"] or {}).get("days", {})
    day = days.get(d.isoformat())
    if not day:
        return None
    nxt = []
    for i in (1, 2, 3):
        dd = d + timedelta(days=i)
        if dd.isoformat() in days:
            nxt.append((dd, days[dd.isoformat()]))
    return render_weather(d=d, city=region_name(region), day=day, desc=sources.wmo(day["code"])[1],
                          next_days=nxt, brand=BRAND, **style)


def rates_bytes(region: str, d: date, rates: dict, **style) -> bytes | None:
    if not rates or "USD" not in rates:
        return None
    return render_rates(d=d, city=region_name(region), rates=rates, brand=BRAND, **style)


def poster_bytes(region: str, d: date, data: dict, brand: str, footer: str, **style) -> bytes | None:
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
        **style,
    )


_ad_cache: dict[str, bytes] = {}


async def _file(bot: Bot, fid: str | None) -> bytes | None:
    if not fid:
        return None
    if fid not in _ad_cache:
        try:
            buf = await bot.download(fid)
            _ad_cache[fid] = buf.read()
        except Exception as e:
            log.warning("fayl yuklanmadi: %s", e)
            return None
    return _ad_cache[fid]


def is_namanganliklar(title: str | None, username: str | None) -> bool:
    return "namanganliklar" in f"{title or ''} {username or ''}".lower()


async def chat_poster(bot: Bot, c, d: date, data: dict) -> bytes | None:
    """Kanal rasmi: har doim Namanganliklar.uz nomi va logosi (kanal nomi yozilmaydi).
    Kanal uchun alohida: rang va reklama."""
    ad_img = await _file(bot, c["ad_file"])
    return await asyncio.to_thread(
        poster_bytes, c["region"], d, data, BRAND, "",
        theme=c["theme"] or "zumrad",
        ad_text=c["ad_text"] or "",
        ad_contact=c["ad_contact"] if c["ad_contact"] is not None else AD_CONTACT,
        ad_image=ad_img, logo=default_logo(), logo_tint=True,
    )


async def chat_style(bot: Bot, c) -> dict:
    return dict(
        theme=c["theme"] or "zumrad",
        ad_text=c["ad_text"] or "",
        ad_contact=c["ad_contact"] if c["ad_contact"] is not None else AD_CONTACT,
        ad_image=await _file(bot, c["ad_file"]), logo=default_logo(), logo_tint=True,
    )


def bot_style() -> dict:
    t = db.kv_get("bot_theme") or "zumrad"
    return dict(theme=t, ad_contact=AD_CONTACT, logo=default_logo(), logo_tint=True)


async def album(region: str, d: date, data: dict, style: dict) -> list[tuple[str, bytes]]:
    """[(nom, rasm)] — namoz (majburiy), ob-havo, valyuta (bo'lsa)."""
    out = []
    p = await asyncio.to_thread(poster_bytes, region, d, data, BRAND, "", **style)
    if p is None:
        return []
    out.append(("namoz", p))
    w = await asyncio.to_thread(weather_bytes, region, d, data, **style)
    if w:
        out.append(("obhavo", w))
    r = await asyncio.to_thread(rates_bytes, region, d, data["rates"], **style)
    if r:
        out.append(("kurs", r))
    return out


def _media(items, caption: str):
    media = []
    for i, (name, b) in enumerate(items):
        src = b if isinstance(b, str) else BufferedInputFile(b, f"{name}.jpg")
        media.append(InputMediaPhoto(media=src, caption=caption if i == 0 else None))
    return media


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


def user_caption(region: str, d: date, data: dict) -> str:
    rec = data["rec"]
    lines = [f"🌙 <b>Ertangi kun — {texts.dstr(d)}</b>"]
    if rec:
        t = rec["times"]
        lines.append(f"🕌 Bomdod {t['bomdod']} · Peshin {t['peshin']} · Asr {t['asr']} · "
                     f"Shom {t['shom']} · Xufton {t['xufton']}")
    wd = (data["w"] or {}).get("days", {}).get(d.isoformat())
    if wd:
        lines.append(f"{sources.wmo(wd['code'])[0]} {wd['tmin']:+d}° … {wd['tmax']:+d}°, {sources.wmo(wd['code'])[1].lower()}")
    usd = data["rates"].get("USD")
    if usd:
        lines.append(f"💵 1 USD = {texts.money(usd['rate'])} so'm ({usd['date']})")
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

    # --- foydalanuvchilar: 3 ta rasm (albom), birinchisida qisqacha matn
    users = db.notify_users() if only_chat is None else []
    if only_user:
        u = db.get_user(only_user)
        users = [{"id": only_user, "region": (u["region"] if u and u["region"] else "namangan")}]
    ustyle = bot_style()
    file_ids: dict[str, list] = {}  # hudud -> Telegram'ga yuklangan rasmlar (qayta yuklamaslik uchun)
    for u in users:
        data = await data_for(u["region"])
        cap = user_caption(u["region"], d, data)
        if u["region"] not in file_ids:
            items = await album(u["region"], d, data, ustyle)
            if not items:
                missing.add(region_name(u["region"]))
                file_ids[u["region"]] = []
        else:
            items = file_ids[u["region"]]
        if not items:
            txt = texts.evening_digest(u["region"], d, data["rec"], data["w"], data["rates"], data["rates_for_d"])
            ok = await _safe(lambda: bot.send_message(u["id"], txt),
                             on_forbidden=lambda uid=u["id"]: db.set_user(uid, active=0))
        else:
            ok = await _safe(lambda: bot.send_media_group(u["id"], _media(items, cap)),
                             on_forbidden=lambda uid=u["id"]: db.set_user(uid, active=0))
            if ok and u["region"] not in file_ids:
                file_ids[u["region"]] = [(n, m.photo[-1].file_id) for (n, _), m in zip(items, ok)]
        sent_u += bool(ok)
        await asyncio.sleep(0.05)

    # --- kanallar / guruhlar: albom (namoz + ob-havo + kurs)
    chats = db.active_chats() if only_user is None else []
    if only_chat:
        c = db.get_chat(only_chat)
        chats = [c] if c else []
    for c in chats:
        data = await data_for(c["region"])
        items = await album(c["region"], d, data, await chat_style(bot, c))
        if not items:
            missing.add(region_name(c["region"]))
            continue
        cap = channel_caption(c["region"], d, data, c["username"])
        ok = await _safe(
            lambda: bot.send_media_group(c["id"], _media(items, cap)),
            on_forbidden=lambda cid=c["id"]: db.set_chat(cid, active=0),
        )
        sent_c += bool(ok)
        await asyncio.sleep(0.2)

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
