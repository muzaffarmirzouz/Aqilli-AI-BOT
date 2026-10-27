from datetime import date, datetime

from .config import TAKBIR
from .regions import name as region_name
from .sources import PRAYER_KEYS, wmo

MONTHS = ["yanvar", "fevral", "mart", "aprel", "may", "iyun", "iyul", "avgust",
          "sentyabr", "oktyabr", "noyabr", "dekabr"]
DAYS = ["Dushanba", "Seshanba", "Chorshanba", "Payshanba", "Juma", "Shanba", "Yakshanba"]
P_NAMES = {"bomdod": "Bomdod", "quyosh": "Quyosh", "peshin": "Peshin",
           "asr": "Asr", "shom": "Shom", "xufton": "Xufton"}
P_ICONS = {"bomdod": "🌄", "quyosh": "🌅", "peshin": "☀️", "asr": "🌤", "shom": "🌇", "xufton": "🌙"}


def dstr(d: date) -> str:
    return f"{d.day}-{MONTHS[d.month - 1]}, {DAYS[d.weekday()].lower()}"


def money(x: float) -> str:
    return f"{x:,.2f}".replace(",", " ")


def prayer_block(region: str, d: date, rec: dict | None, now: datetime | None = None) -> str:
    head = f"🕌 <b>Namoz vaqtlari — {region_name(region)}</b>\n📅 {dstr(d)}"
    if rec and rec.get("hijri"):
        head += f" · {rec['hijri']}"
    if not rec:
        return head + "\n\n⚠️ Bu kun uchun vaqtlar hozircha topilmadi. Birozdan keyin qayta urinib ko'ring."
    t = rec["times"]
    lines = []
    nxt = None
    if now and now.date() == d:
        cur = now.hour * 60 + now.minute
        for k in PRAYER_KEYS:
            h, m = map(int, t[k].split(":"))
            if h * 60 + m > cur:
                nxt = k
                break
    for i, k in enumerate(PRAYER_KEYS):
        tk = f"  <i>(takbir +{TAKBIR[i]})</i>" if i < len(TAKBIR) and TAKBIR[i] else ""
        row = f"{P_ICONS[k]} {P_NAMES[k]}: <b>{t[k]}</b>{tk}"
        if k == nxt:
            h, m = map(int, t[k].split(":"))
            left = h * 60 + m - (now.hour * 60 + now.minute)
            row += f"  ⏳ {left // 60} soat {left % 60} daq qoldi" if left >= 60 else f"  ⏳ {left} daq qoldi"
        lines.append(row)
    return head + "\n\n" + "\n".join(lines)


def weather_day(region: str, d: date, w: dict | None) -> str:
    day = (w or {}).get("days", {}).get(d.isoformat())
    if not day:
        return f"🌤 <b>Ob-havo — {region_name(region)}</b>\n⚠️ Ma'lumot olinmadi."
    ic, txt = wmo(day["code"])
    s = (f"🌤 <b>Ob-havo — {region_name(region)}, {dstr(d)}</b>\n"
         f"{ic} {txt}\n🌡 Kunduzi <b>{day['tmax']:+d}°</b>, kechasi <b>{day['tmin']:+d}°</b>")
    if day.get("rain") is not None:
        s += f"\n☔ Yog'ingarchilik ehtimoli: {day['rain']}%"
    if day.get("wind") is not None:
        s += f"\n💨 Shamol: {round(day['wind'])} m/s gacha"
    return s


def weather_now(region: str, w: dict | None, today: date) -> str:
    n = (w or {}).get("now")
    if not n:
        return f"🌤 <b>Ob-havo — {region_name(region)}</b>\n⚠️ Ma'lumot olinmadi."
    ic, txt = wmo(n.get("weather_code"))
    s = (f"🌤 <b>Hozir — {region_name(region)}</b>\n{ic} {txt}, <b>{round(n['temperature_2m']):+d}°</b>"
         f" (his qilinadi {round(n.get('apparent_temperature', n['temperature_2m'])):+d}°)\n"
         f"💧 Namlik: {n.get('relative_humidity_2m', '—')}%  💨 Shamol: {round(n.get('wind_speed_10m', 0))} m/s")
    rest = []
    for k, label in ((today.isoformat(), "Bugun"),):
        day = w["days"].get(k)
        if day:
            rest.append(f"{label}: {day['tmin']:+d}° … {day['tmax']:+d}°, {wmo(day['code'])[1].lower()}")
    return s + ("\n\n" + "\n".join(rest) if rest else "")


CCY_FLAGS = {"USD": "🇺🇸", "RUB": "🇷🇺", "EUR": "🇪🇺", "CNY": "🇨🇳", "KZT": "🇰🇿"}


def _diff(x: float) -> str:
    if not x:
        return ""
    s = f"{abs(x):.2f}".rstrip("0").rstrip(".")
    return f" ({'▲' if x > 0 else '▼'}{s})"


def rates_block(r: dict, title: str = "Valyuta kursi") -> str:
    """O'zR MB kursi — CardXabar uslubida: sarlavha + sana, har valyuta alohida qatorda, o'zgarishi qavsda."""
    if not r:
        return "🏦 <b>Valyuta kursi</b>\n⚠️ Markaziy bank ma'lumoti olinmadi."
    date_s = (r.get("USD") or next(iter(r.values()))).get("date", "")
    intl = any(v.get("src") == "xalqaro" for v in r.values())
    head = "🌐 <b>Xalqaro valyuta kursi" if intl else "🏦 <b>O'zR MB valyutalar kursi"
    lines = [f"{head} - {date_s}</b>", ""]
    for c, flag in CCY_FLAGS.items():
        if c in r:
            lines.append(f"{flag} 1 {c} = {money(r[c]['rate'])} so'm{'' if intl else _diff(r[c].get('diff') or 0)}")
    if intl:
        lines.append("\n<i>Markaziy bank sayti hozir javob bermayapti — xalqaro bozor kursi ko'rsatildi.</i>")
    return "\n".join(lines)


def evening_digest(region: str, d: date, rec, w, r, rates_for_tomorrow: bool) -> str:
    title = "Ertangi valyuta kursi" if rates_for_tomorrow else "Valyuta kursi"
    return (
        f"🌙 <b>Ertangi kun — {dstr(d)}</b>\n\n"
        + prayer_block(region, d, rec) + "\n\n"
        + weather_day(region, d, w) + "\n\n"
        + rates_block(r, title)
    )
