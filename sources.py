"""Ma'lumot manbalari: namoz vaqtlari, ob-havo, valyuta kursi."""
import asyncio
import logging
import os
import re
import time
from datetime import date, timedelta

import aiohttp

from . import db
from .regions import REGIONS

log = logging.getLogger(__name__)
_TIMEOUT = aiohttp.ClientTimeout(total=20)
_HEADERS = {"User-Agent": "Mozilla/5.0 (NamozVaqtiBot)"}

from .config import PRAYER_KEYS  # noqa: E402
# islomapi.uz maydon nomlari -> bizning nomlar
_API_KEYS = {
    "tong_saharlik": "bomdod", "quyosh": "quyosh", "peshin": "peshin",
    "asr": "asr", "shom_iftor": "shom", "hufton": "xufton",
}
_HHMM = re.compile(r"^\d{1,2}:\d{2}$")


async def _get_json(url: str, params: dict | None = None, tries: int = 3):
    last = None
    for i in range(tries):
        try:
            async with aiohttp.ClientSession(timeout=_TIMEOUT, headers=_HEADERS) as s:
                async with s.get(url, params=params) as r:
                    r.raise_for_status()
                    return await r.json(content_type=None)
        except Exception as e:  # tarmoq xatosi — qayta urinamiz
            last = e
            await asyncio.sleep(2 * (i + 1))
    raise RuntimeError(f"{url}: {last}")


# ======================= NAMOZ VAQTLARI =======================
def valid_times(t: dict) -> bool:
    """Barcha 6 vaqt bor, HH:MM formatda va ketma-ket o'sib boradi."""
    try:
        vals = [t[k] for k in PRAYER_KEYS]
    except KeyError:
        return False
    if not all(_HHMM.match(v or "") for v in vals):
        return False
    mins = [int(v.split(":")[0]) * 60 + int(v.split(":")[1]) for v in vals]
    return all(a < b for a, b in zip(mins, mins[1:]))


def _norm_time(v: str) -> str:
    h, m = v.strip()[:5].split(":")
    return f"{int(h):02d}:{int(m):02d}"


def parse_islomapi_item(item: dict) -> tuple[int | None, dict]:
    """Bitta kun yozuvini {'times':{...}, 'hijri': '...'} ko'rinishiga keltiradi."""
    raw = item.get("times") or {}
    times = {}
    for api_k, our_k in _API_KEYS.items():
        if raw.get(api_k):
            times[our_k] = _norm_time(raw[api_k])
    day = item.get("day")
    if not isinstance(day, int):
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(item.get("date", "")))
        day = int(m.group(3)) if m else None
    h = item.get("hijri_date") or {}
    hijri = ""
    if h.get("day") and h.get("month"):
        hijri = f"{h['day']}-{str(h['month']).capitalize()}"
    return day, {"times": times, "hijri": hijri}


async def fetch_month(region: str, year: int, month: int) -> int:
    """islomapi.uz dan oylik taqvimni olib, bazaga yozadi. Saqlangan kunlar sonini qaytaradi."""
    api_name = REGIONS[region][1]
    data = await _get_json("https://islomapi.uz/api/monthly", {"region": api_name, "month": month})
    saved = 0
    for item in data if isinstance(data, list) else []:
        day, rec = parse_islomapi_item(item)
        if day and valid_times(rec["times"]):
            db.save_prayer(region, date(year, month, day).isoformat(), rec, "islomapi")
            saved += 1
    return saved


async def fetch_day(region: str, d: date) -> bool:
    api_name = REGIONS[region][1]
    item = await _get_json(
        "https://islomapi.uz/api/daily", {"region": api_name, "month": d.month, "day": d.day}
    )
    if isinstance(item, list):
        item = item[0] if item else {}
    _, rec = parse_islomapi_item(item)
    if valid_times(rec["times"]):
        db.save_prayer(region, d.isoformat(), rec, "islomapi")
        return True
    return False


async def get_prayer(region: str, d: date) -> dict | None:
    """Faqat bazadagi jadvaldan (prayer_data/*.csv yoki /oylik orqali kiritilgan). Topilmasa None.
    PRAYER_ONLINE=1 bo'lsa, jadvalda yo'q kunlar islomapi.uz dan olinadi."""
    rec = db.load_prayer(region, d.isoformat())
    if rec or os.getenv("PRAYER_ONLINE", "0") != "1":
        return rec
    try:
        await fetch_month(region, d.year, d.month)
    except Exception as e:
        log.warning("oylik olinmadi %s %s: %s", region, d, e)
    return db.load_prayer(region, d.isoformat())


# ======================= OB-HAVO (Open-Meteo, kalitsiz) =======================
WMO = {
    0: ("☀️", "Ochiq osmon"), 1: ("🌤", "Asosan ochiq"), 2: ("⛅", "Qisman bulutli"),
    3: ("☁️", "Bulutli"), 45: ("🌫", "Tuman"), 48: ("🌫", "Qirovli tuman"),
    51: ("🌦", "Yengil shivalama"), 53: ("🌦", "Shivalama"), 55: ("🌧", "Kuchli shivalama"),
    56: ("🌧", "Muzli shivalama"), 57: ("🌧", "Muzli shivalama"),
    61: ("🌦", "Yengil yomg'ir"), 63: ("🌧", "Yomg'ir"), 65: ("🌧", "Kuchli yomg'ir"),
    66: ("🌧", "Muzli yomg'ir"), 67: ("🌧", "Muzli yomg'ir"),
    71: ("🌨", "Yengil qor"), 73: ("🌨", "Qor"), 75: ("❄️", "Kuchli qor"), 77: ("🌨", "Qor donachalari"),
    80: ("🌦", "Jala"), 81: ("🌧", "Kuchli jala"), 82: ("⛈", "Juda kuchli jala"),
    85: ("🌨", "Qor jala"), 86: ("❄️", "Kuchli qor jala"),
    95: ("⛈", "Momaqaldiroq"), 96: ("⛈", "Do'lli momaqaldiroq"), 99: ("⛈", "Kuchli do'l"),
}
_cache: dict[str, tuple[float, object]] = {}


def wmo(code) -> tuple[str, str]:
    return WMO.get(int(code or 0), ("🌡", "—"))


def _r(v):
    return None if v is None else round(v)


async def get_weather(region: str) -> dict:
    """{'now': {...}, 'days': {'YYYY-MM-DD': {...}}}"""
    key = f"w:{region}"
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < 1200:
        return hit[1]
    _, _, lat, lon = REGIONS[region]
    j = await _get_json(
        "https://api.open-meteo.com/v1/forecast",
        {
            "latitude": lat, "longitude": lon, "timezone": "Asia/Tashkent", "forecast_days": 5,
            "wind_speed_unit": "ms",
            "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
                     "precipitation_probability_max,wind_speed_10m_max,apparent_temperature_max",
        },
    )
    res = {"now": j.get("current", {}), "days": {}}
    d = j.get("daily", {})
    for i, day in enumerate(d.get("time", [])):
        res["days"][day] = {
            "code": d["weather_code"][i],
            "tmax": round(d["temperature_2m_max"][i]),
            "tmin": round(d["temperature_2m_min"][i]),
            "rain": d.get("precipitation_probability_max", [None] * 9)[i],
            "wind": d.get("wind_speed_10m_max", [None] * 9)[i],
            "feels": _r(d.get("apparent_temperature_max", [None] * 9)[i]),
        }
    _cache[key] = (time.time(), res)
    return res


# ======================= VALYUTA (Markaziy bank) =======================
_RATE_TIMEOUT = aiohttp.ClientTimeout(total=8)


async def _fetch_json(url: str):
    """Bitta qisqa urinish (qayta urinish va uzoq kutish yo'q)."""
    async with aiohttp.ClientSession(timeout=_RATE_TIMEOUT, headers=_HEADERS) as s:
        async with s.get(url) as r:
            r.raise_for_status()
            return await r.json(content_type=None)


async def _cbu_rate(ccy: str, ds: str):
    try:
        j = await _fetch_json(f"https://cbu.uz/uz/arkhiv-kursov-valyut/json/{ccy}/{ds}/")
        it = j[0] if isinstance(j, list) else j
        return ccy, {
            "rate": float(it["Rate"]),
            "diff": float(it.get("Diff") or 0),
            "date": it.get("Date", ""),  # "07.10.2026" — kurs amal qiladigan sana
        }
    except Exception as e:
        log.warning("kurs olinmadi %s: %s %r", ccy, ds, e)
        return ccy, None


async def _nbu_rates() -> dict:
    """Zaxira: nbu.uz ro'yxatidagi Markaziy bank kursi (cb_price) — faqat bugungi kun uchun."""
    out = {}
    try:
        j = await _fetch_json("https://nbu.uz/en/exchange-rates/json/")
        for it in j:
            ccy = str(it.get("code", "")).upper()
            if ccy in ("USD", "EUR", "RUB") and it.get("cb_price"):
                out[ccy] = {"rate": float(it["cb_price"]), "diff": 0.0,
                            "date": str(it.get("date", ""))[:10]}
    except Exception as e:
        log.warning("zaxira kurs (nbu.uz) olinmadi: %r", e)
    return out


async def _cbu_all() -> dict:
    """Zaxira: cbu.uz'ning sanasiz umumiy ro'yxati (eng so'nggi kurslar)."""
    out = {}
    try:
        j = await _fetch_json("https://cbu.uz/uz/arkhiv-kursov-valyut/json/")
        for it in j:
            ccy = str(it.get("Ccy", "")).upper()
            if ccy in ("USD", "EUR", "RUB"):
                out[ccy] = {"rate": float(it["Rate"]), "diff": float(it.get("Diff") or 0),
                            "date": it.get("Date", "")}
    except Exception as e:
        log.warning("zaxira kurs (cbu.uz umumiy) olinmadi: %r", e)
    return out


_INTL = (
    ("open.er-api.com", "https://open.er-api.com/v6/latest/USD"),
    ("jsdelivr (fawazahmed0)", "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies/usd.json"),
)


def _intl_parse(j: dict) -> dict:
    """USD asosidagi kurslardan so'mdagi USD/EUR/RUB ni hisoblaydi."""
    r = j.get("rates") or j.get("usd") or {}
    r = {k.upper(): float(v) for k, v in r.items() if isinstance(v, (int, float))}
    uzs = r.get("UZS")
    if not uzs:
        return {}
    ds = _today_tz().strftime("%d.%m.%Y")
    out = {"USD": {"rate": round(uzs, 2), "diff": 0.0, "date": ds, "src": "xalqaro"}}
    for c in ("EUR", "RUB"):
        if r.get(c):
            out[c] = {"rate": round(uzs / r[c], 2), "diff": 0.0, "date": ds, "src": "xalqaro"}
    return out


async def _intl_rates() -> dict:
    """Oxirgi zaxira: xalqaro manbalar (O'zbekiston saytlari server uchun yopiq bo'lsa).
    Markaziy bank kursiga yaqin, lekin aynan bir xil emas — matnda «xalqaro kurs» deb ko'rsatiladi."""
    for name, url in _INTL:
        try:
            out = _intl_parse(await _fetch_json(url))
            if out:
                return out
        except Exception as e:
            log.warning("zaxira kurs (%s) olinmadi: %r", name, e)
    return {}


def _today_tz() -> date:
    from .config import TZ
    from datetime import datetime
    return datetime.now(TZ).date()


def _remember(out: dict):
    """Oxirgi muvaffaqiyatli olingan kursni bazaga yozamiz (manbalar ishlamay qolsa — shu ishlatiladi)."""
    try:
        import json
        db.kv_set("rates_last", json.dumps(out, ensure_ascii=False))
    except Exception:
        pass


def last_known_rates() -> dict:
    try:
        import json
        v = db.kv_get("rates_last")
        return json.loads(v) if v else {}
    except Exception:
        return {}


async def get_rates(d: date | None = None, fallback_last: bool = False) -> dict:
    """CBU kursi. d berilsa — shu sanaga belgilangan kurs (e'lon qilingan bo'lsa).
    cbu.uz ishlamasa — cbu.uz umumiy ro'yxati, keyin nbu.uz (faqat bugungi kun uchun).
    fallback_last=True bo'lsa va hech biri javob bermasa — oxirgi ma'lum kurs qaytadi."""
    today = _today_tz()
    ds = (d or today).isoformat()
    key = f"r:{ds}"
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < (1200 if hit[1] else 90):
        out = hit[1]
    else:
        out = {c: v for c, v in await asyncio.gather(*(_cbu_rate(c, ds) for c in ("USD", "EUR", "RUB"))) if v}
        if not out and ds == today.isoformat():
            out = await _cbu_all() or await _nbu_rates() or await _intl_rates()
        _cache[key] = (time.time(), out)   # bo'sh natija 90 soniya eslab qolinadi (bot qotib qolmasligi uchun)
        if out and ds == today.isoformat():
            _remember(out)
    if not out and fallback_last:
        return last_known_rates()
    return out


async def diagnose_rates() -> str:
    """Har bir kurs manbasini alohida tekshiradi (admin uchun)."""
    ds = _today_tz().isoformat()
    lines = [f"💵 <b>Kurs manbalarini tekshirish</b> ({ds})"]
    checks = (
        ("cbu.uz (sana bo'yicha)", f"https://cbu.uz/uz/arkhiv-kursov-valyut/json/USD/{ds}/"),
        ("cbu.uz (umumiy)", "https://cbu.uz/uz/arkhiv-kursov-valyut/json/"),
        ("nbu.uz (zaxira)", "https://nbu.uz/en/exchange-rates/json/"),
        *((f"{n} (xalqaro)", u) for n, u in _INTL),
    )
    for name, url in checks:
        t = time.time()
        try:
            j = await _fetch_json(url)
            n = len(j) if isinstance(j, list) else 1
            lines.append(f"✅ {name} — {time.time() - t:.1f} s, {n} ta yozuv")
        except Exception as e:
            lines.append(f"❌ {name} — {type(e).__name__}: {str(e)[:120]}")
    last = last_known_rates().get("USD")
    lines.append(f"\n🗂 Oxirgi saqlangan kurs: " + (f"1 USD = {last['rate']} ({last.get('date', '')})" if last else "yo'q"))
    return "\n".join(lines)


def tomorrow(today: date) -> date:
    return today + timedelta(days=1)
