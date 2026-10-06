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
            "latitude": lat, "longitude": lon, "timezone": "Asia/Tashkent", "forecast_days": 3,
            "wind_speed_unit": "ms",
            "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
                     "precipitation_probability_max,wind_speed_10m_max",
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
        }
    _cache[key] = (time.time(), res)
    return res


# ======================= VALYUTA (Markaziy bank) =======================
async def get_rates(d: date | None = None) -> dict:
    """CBU kursi. d berilsa — shu sanaga belgilangan kurs (e'lon qilingan bo'lsa)."""
    ds = (d or date.today()).isoformat()
    key = f"r:{ds}"
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < 1200:
        return hit[1]
    out = {}
    for ccy in ("USD", "EUR", "RUB"):
        try:
            j = await _get_json(f"https://cbu.uz/uz/arkhiv-kursov-valyut/json/{ccy}/{ds}/")
            it = j[0] if isinstance(j, list) else j
            out[ccy] = {
                "rate": float(it["Rate"]),
                "diff": float(it.get("Diff") or 0),
                "date": it.get("Date", ""),  # "07.10.2026" — kurs amal qiladigan sana
            }
        except Exception as e:
            log.warning("kurs olinmadi %s: %s", ccy, e)
    if out:
        _cache[key] = (time.time(), out)
    return out


def tomorrow(today: date) -> date:
    return today + timedelta(days=1)
