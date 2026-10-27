"""Har kungi kechki yuborish va oldindan tekshiruv."""
import asyncio
import logging
from datetime import date, datetime, timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import BufferedInputFile, InputMediaPhoto

from . import db, prayer_table, sources, subscribe, texts
import html
import os

from .config import AD_CONTACT, ADMIN_IDS, BRAND, PRECHECK_MIN, REMIND_DAYS, REQUIRED_CHANNEL, SEND_AT, TZ
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
    rates = await sources.get_rates(d) or sources.last_known_rates()
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


async def album(region: str, d: date, data: dict, style: dict,
                weather: bool = True, rates: bool = True) -> list[tuple[str, bytes]]:
    """[(nom, rasm)] — namoz (majburiy), ob-havo va valyuta (yoqilgan va ma'lumot bo'lsa)."""
    out = []
    p = await asyncio.to_thread(poster_bytes, region, d, data, BRAND, "", **style)
    if p is None:
        return []
    out.append(("namoz", p))
    if weather:
        w = await asyncio.to_thread(weather_bytes, region, d, data, **style)
        if w:
            out.append(("obhavo", w))
    if rates:
        r = await asyncio.to_thread(rates_bytes, region, d, data["rates"], **style)
        if r:
            out.append(("kurs", r))
    return out


async def send_items(bot: Bot, chat_id: int, items, caption: str):
    """1 ta rasm — oddiy rasm, 2+ — albom. Natija: xabarlar ro'yxati."""
    if len(items) == 1:
        name, b = items[0]
        src = b if isinstance(b, str) else BufferedInputFile(b, f"{name}.jpg")
        m = await bot.send_photo(chat_id, src, caption=caption)
        return [m]
    return await bot.send_media_group(chat_id, _media(items, caption))


def chat_flags(c) -> dict:
    sch = db.get_schedule(c, "c")
    return dict(weather=bool(sch["obhavo"]), rates=bool(sch["kurs"]))


# ---------- reklama bloki (botdagi ob-havo va kurs xabarlari ostida) ----------
PROMO_BOT = os.getenv("PROMO_BOT", "@Aqilli_AI_Bot").strip()
PROMO_DEFAULT = "Kunlik namoz vaqtlari, ob-havo ma'lumoti va valyuta kurslari."


def promo_text() -> str:
    v = db.kv_get("promo_text")
    return PROMO_DEFAULT if v is None else v


def promo_block() -> str:
    """Bot manzili + admin yozgan reklama matni."""
    lines = []
    if PROMO_BOT:
        t = promo_text().strip()
        lines.append(f"🤖 <b>{html.escape(PROMO_BOT, quote=False)}</b>" + (f" — {html.escape(t, quote=False)}" if t else ""))
    return "\n".join(lines)


def with_promo(text: str) -> str:
    p = promo_block()
    return f"{text}\n\n{p}" if p else text


def _media(items, caption: str):
    media = []
    for i, (name, b) in enumerate(items):
        src = b if isinstance(b, str) else BufferedInputFile(b, f"{name}.jpg")
        media.append(InputMediaPhoto(media=src, caption=caption if i == 0 else None))
    return media


# Kanal postlari ostidagi bot havolasi (barcha kanallarda bir xil)
CHANNEL_PROMO = {
    "namoz": "Kunlik namoz vaqtlari",
    "obhavo": "Kunlik ob-havo ma'lumotlari",
    "kurs": "Kunlik valyuta kurslarini kuzatib boring",
}


def channel_promo(item: str) -> str:
    if not PROMO_BOT:
        return ""
    u = PROMO_BOT.lstrip("@")
    return f"🤖 <a href=\"https://t.me/{u}\">@{u}</a> — {CHANNEL_PROMO[item]}"


def channel_caption(region: str, d: date, data: dict, username: str | None) -> str:
    wd = (data["w"] or {}).get("days", {}).get(d.isoformat())
    when = "Ertangi namoz vaqtlari" if d > now().date() else "Namoz vaqtlari"
    lines = [f"🕌 <b>{when} — {texts.dstr(d)}</b>\n📍 {region_name(region)}"]
    if wd:
        ic, t = sources.wmo(wd["code"])
        lines.append(f"{ic} Ob-havo: {wd['tmin']:+d}° … {wd['tmax']:+d}°, {t.lower()}")
    usd = data["rates"].get("USD")
    if usd:
        lines.append(f"💵 1 USD = {texts.money(usd['rate'])} so'm (MB, {usd['date']})")
    p = channel_promo("namoz")
    if p:
        lines.append(f"\n{p}")
    return "\n".join(lines)


def user_caption(region: str, d: date, data: dict, label: str = "Ertangi kun") -> str:
    rec = data["rec"]
    lines = [f"🌙 <b>{label} — {texts.dstr(d)}</b>"]
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
        if not only_user and not await subscribe.is_subscribed(bot, u["id"]):
            continue  # majburiy obuna kanalidan chiqib ketgan
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
            ok = await _safe(lambda: send_items(bot, u["id"], items, cap),
                             on_forbidden=lambda uid=u["id"]: db.set_user(uid, active=0))
            if ok and u["region"] not in file_ids:
                file_ids[u["region"]] = [(n, m.photo[-1].file_id) for (n, _), m in zip(items, ok)]
        sent_u += bool(ok)
        await asyncio.sleep(0.05)

    # --- kanallar / guruhlar: namoz rasmi + (panelda yoqilgan bo'lsa) ob-havo va kurs
    chats = db.active_chats() if only_user is None else []
    if only_chat:
        c = db.get_chat(only_chat)
        chats = [c] if c else []
    for c in chats:
        data = await data_for(c["region"])
        items = await album(c["region"], d, data, await chat_style(bot, c), **chat_flags(c))
        if not items:
            missing.add(region_name(c["region"]))
            continue
        cap = channel_caption(c["region"], d, data, c["username"])
        ok = await _safe(
            lambda: send_items(bot, c["id"], items, cap),
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


# ================= Jadval bo'yicha yuborish =================
def target_date(item: str, hhmm: str, today: date) -> date:
    """Qaysi kun ma'lumoti: namoz — 12:00 dan keyin ertangi; ob-havo va kurs — 18:00 dan keyin ertangi."""
    h = int(hhmm[:2])
    if item == "namoz":
        return today + timedelta(days=1) if h >= 12 else today
    return today + timedelta(days=1) if h >= 18 else today


def item_hint(item: str, hhmm: str) -> str:
    if not hhmm:
        return "o'chiq"
    h = int(hhmm[:2])
    if item == "namoz":
        return "ertangi kun" if h >= 12 else "bugungi kun"
    if item == "obhavo":
        return "ertangi kun" if h >= 18 else "bugungi kun"
    return "ertangi kurs (e'lon qilingan bo'lsa)" if h >= 18 else "bugungi kurs"


async def _photo_or_text(bot: Bot, chat_id: int, img, caption: str, name: str):
    """Rasm + izoh (1024 belgidan oshsa — rasm, keyin matn)."""
    if not img:
        return await bot.send_message(chat_id, caption, disable_web_page_preview=True)
    src = img if isinstance(img, str) else BufferedInputFile(img, f"{name}.jpg")
    if len(caption) <= 1000:
        return await bot.send_photo(chat_id, src, caption=caption)
    m = await bot.send_photo(chat_id, src)
    await bot.send_message(chat_id, caption, disable_web_page_preview=True)
    return m


async def _rates_for(hhmm: str, today: date):
    """(sana, kurslar, sarlavha) — kechqurun ertangi kurs e'lon qilingan bo'lsa, o'shani."""
    now_r = await sources.get_rates(today)
    if int(hhmm[:2]) >= 18:
        tom = today + timedelta(days=1)
        tom_r = await sources.get_rates(tom)
        if tom_r and tom_r.get("USD", {}).get("date") == tom.strftime("%d.%m.%Y"):
            return tom, tom_r, "Ertangi valyuta kursi"
    return today, now_r, "Bugungi valyuta kursi"


async def send_item(bot: Bot, item: str, hhmm: str, users, chats) -> tuple[int, int, set]:
    """Bitta turdagi xabarni (namoz / ob-havo / kurs) shu daqiqaga belgilaganlarga yuboradi."""
    today = now().date()
    d = target_date(item, hhmm, today)
    label = "Ertangi kun" if d > today else "Bugun"
    cache: dict[str, dict] = {}
    missing: set[str] = set()

    async def data_for(region):
        if region not in cache:
            cache[region] = await collect(region, d)
        return cache[region]

    rates_pack = await _rates_for(hhmm, today) if item == "kurs" else None
    ustyle = bot_style()
    uploaded: dict[str, str | None] = {}  # hudud -> Telegram file_id (qayta yuklamaslik uchun)

    async def user_payload(region):
        data = await data_for(region)
        if item == "namoz":
            img = await asyncio.to_thread(poster_bytes, region, d, data, BRAND, "", **ustyle)
            if img is None:
                missing.add(region_name(region))
                return None, texts.evening_digest(region, d, data["rec"], data["w"], data["rates"], data["rates_for_d"])
            return img, user_caption(region, d, data, label)
        if item == "obhavo":
            img = await asyncio.to_thread(weather_bytes, region, d, data, **ustyle)
            txt = texts.weather_day(region, d, data["w"])
            return img, with_promo(txt)
        rd, rr, title = rates_pack
        img = await asyncio.to_thread(rates_bytes, region, rd, rr, **ustyle)
        return img, with_promo(texts.rates_block(rr, title))

    sent_u = 0
    payloads: dict[str, tuple] = {}
    for u in users:
        if not await subscribe.is_subscribed(bot, u["id"]):
            continue
        r = u["region"]
        if r not in payloads:
            payloads[r] = await user_payload(r)
        img, cap = payloads[r]
        src = uploaded.get(r) or img
        m = await _safe(lambda: _photo_or_text(bot, u["id"], src, cap, item),
                        on_forbidden=lambda uid=u["id"]: db.set_user(uid, active=0))
        if m is not None and getattr(m, "photo", None) and r not in uploaded:
            uploaded[r] = m.photo[-1].file_id
        sent_u += bool(m)
        await asyncio.sleep(0.05)

    sent_c = 0
    for c in chats:
        data = await data_for(c["region"])
        style = await chat_style(bot, c)
        if item == "namoz":
            img = await asyncio.to_thread(poster_bytes, c["region"], d, data, BRAND, "", **style)
            if img is None:
                missing.add(region_name(c["region"]))
                continue
            cap = channel_caption(c["region"], d, data, c["username"])
        elif item == "obhavo":
            img = await asyncio.to_thread(weather_bytes, c["region"], d, data, **style)
            if img is None:
                continue
            cap = f"🌤 <b>{texts.dstr(d).capitalize()} — {region_name(c['region'])} ob-havosi</b>"
        else:
            rd, rr, title = rates_pack
            img = await asyncio.to_thread(rates_bytes, c["region"], rd, rr, **style)
            if img is None:
                continue
            cap = f"💵 <b>{title} — {texts.dstr(rd)}</b>"
        if item != "namoz" and channel_promo(item):
            cap += f"\n\n{channel_promo(item)}"
        m = await _safe(lambda: bot.send_photo(c["id"], BufferedInputFile(img, f"{item}.jpg"), caption=cap),
                        on_forbidden=lambda cid=c["id"]: db.set_chat(cid, active=0))
        sent_c += bool(m)
        await asyncio.sleep(0.2)
    return sent_u, sent_c, missing


async def run_minute(bot: Bot, hhmm: str):
    for item in db.ITEMS:
        users, chats = db.due_users(item, hhmm), db.due_chats(item, hhmm)
        if not users and not chats:
            continue
        try:
            su, sc, missing = await send_item(bot, item, hhmm, users, chats)
            log.info("%s %s: %s foydalanuvchi, %s kanal", hhmm, item, su, sc)
            if missing:
                key = f"miss:{now().date().isoformat()}"
                if db.kv_get(key) is None:
                    db.kv_set(key, "1")
                    await notify_admins(bot, f"⚠️ Namoz vaqtlari topilmagani uchun rasm chiqmadi: "
                                             f"{', '.join(sorted(missing))}. /oylik bilan kiriting.")
        except Exception as e:
            log.exception("%s %s: %s", hhmm, item, e)
            await notify_admins(bot, f"❌ {hhmm} dagi «{item}» yuborishda xato: {html.escape(str(e))[:200]}")


async def scheduler(bot: Bot):
    """Har daqiqada: shu soatga belgilangan xabarlarni yuboradi. SEND_AT dan PRECHECK_MIN oldin — tekshiruv.
    Bot qayta ishga tushsa, 20 daqiqagacha o'tkazib yuborilganlarni ham yuboradi."""
    while True:
        n = now().replace(second=0, microsecond=0)
        today = n.date()

        check_t = _at(today, SEND_AT) - timedelta(minutes=PRECHECK_MIN)
        if n >= check_t and db.kv_get("last_check") != today.isoformat():
            db.kv_set("last_check", today.isoformat())
            try:
                await precheck(bot)
            except Exception as e:
                log.exception("precheck: %s", e)

        last_s = db.kv_get("sched_last")
        try:
            last = datetime.fromisoformat(last_s) if last_s else None
        except ValueError:
            last = None
        if last is None or last > n or (n - last) > timedelta(minutes=20):
            last = n - timedelta(minutes=1)
        m = last + timedelta(minutes=1)
        while m <= n:
            await run_minute(bot, m.strftime("%H:%M"))
            db.kv_set("sched_last", m.isoformat())
            m += timedelta(minutes=1)

        nxt = n + timedelta(minutes=1)
        await asyncio.sleep(max(1, (nxt - now()).total_seconds() + 1))
