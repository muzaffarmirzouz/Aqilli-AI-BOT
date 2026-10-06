"""Oylik namoz jadvalini o'qish: CSV fayldan yoki admin yuborgan matndan."""
import glob
import logging
import os
import re
from datetime import date

from . import db
from .config import PRAYER_KEYS
from .sources import valid_times

log = logging.getLogger(__name__)
_TIME = re.compile(r"\b(\d{1,2})[:.](\d{2})\b")


def parse_text(text: str, year: int, month: int) -> tuple[dict[int, dict], list[str]]:
    """Har qatordan: kun raqami + vaqtlar.
    6 ta vaqt: bomdod, quyosh, peshin, asr, shom, xufton.
    8 ta vaqt (islom.uz jadvali): bomdod, quyosh, ishroq, peshin, asr, shom, xufton, tahajjud."""
    days, errors = {}, []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        times = [f"{int(h):02d}:{m}" for h, m in _TIME.findall(line)]
        rest = _TIME.sub(" ", line)
        m = re.match(r"\s*(\d{1,2})\b", rest)
        if not m or not times:
            continue  # sarlavha qatori
        day = int(m.group(1))
        if len(times) == 8:
            times = [times[0], times[1], times[3], times[4], times[5], times[6]]
        elif len(times) == 7:  # tahajjudsiz
            times = [times[0], times[1], times[3], times[4], times[5], times[6]]
        if len(times) != 6:
            errors.append(f"{day}-kun: {len(times)} ta vaqt topildi (6 yoki 8 bo'lishi kerak)")
            continue
        t = dict(zip(PRAYER_KEYS, times))
        try:
            date(year, month, day)
        except ValueError:
            errors.append(f"{day}-kun: bunday sana yo'q")
            continue
        if not valid_times(t):
            errors.append(f"{day}-kun: vaqtlar tartibi noto'g'ri ({' '.join(times)})")
            continue
        days[day] = t
    return days, errors


def save_month(region: str, year: int, month: int, days: dict[int, dict], source: str) -> int:
    for day, t in days.items():
        db.save_prayer(region, date(year, month, day).isoformat(), {"times": t, "hijri": ""}, source)
    return len(days)


def load_dir(path: str = "prayer_data"):
    """prayer_data/<hudud>_<YYYY>-<MM>.csv fayllarini bazaga yuklaydi (qo'lda kiritilganlar saqlanadi)."""
    for f in sorted(glob.glob(os.path.join(path, "*.csv"))):
        m = re.match(r"([a-z_]+)_(\d{4})-(\d{2})\.csv$", os.path.basename(f))
        if not m:
            continue
        region, y, mo = m.group(1), int(m.group(2)), int(m.group(3))
        with open(f, encoding="utf-8") as fh:
            days, errs = parse_text(fh.read().replace(",", " "), y, mo)
        n = save_month(region, y, mo, days, "jadval")
        log.info("%s: %d kun yuklandi, xatolar: %s", f, n, errs or "yo'q")


def last_date(region: str) -> date | None:
    r = db._con.execute("SELECT MAX(date) FROM prayer WHERE region=?", (region,)).fetchone()[0]
    return date.fromisoformat(r) if r else None
