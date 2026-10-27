"""💵 Markaziy bank kursini O'zbekistondagi kompyuterdan qabul qilish.

Railway serveri cbu.uz'ga ulana olmaydi (chet el serverlari bloklangan). Shuning uchun O'zbekistondagi
doim yoqiq kompyuterda `tools/kurs_push.py` ishlaydi: u har 30 daqiqada cbu.uz'dan kursni olib, shu yerga yuboradi.

Railway o'zgaruvchisi:
  KURS_PUSH_TOKEN — o'zingiz o'ylab topgan maxfiy so'z (kompyuterdagi skriptga ham xuddi shuni yozasiz)

Manzil: POST https://<bot-domeni>/kurs/push   (sarlavha: X-Token: <KURS_PUSH_TOKEN>)
Tana:   {"lists": [<cbu.uz JSON ro'yxati>, ...]}  — bugungi va (e'lon qilingan bo'lsa) ertangi kurs
"""
import hmac
import json
import logging
import os
import time
from datetime import datetime

from aiohttp import web

from . import db

log = logging.getLogger(__name__)

PATH = "/kurs/push"
TOKEN = os.getenv("KURS_PUSH_TOKEN", "").strip()
FRESH_SEC = 3 * 3600  # shu vaqt ichida kelgan kurs «yangi» hisoblanadi (cbu.uz'ga urinmasdan ishlatiladi)


def _parse(lst) -> dict[str, dict]:
    """cbu.uz ro'yxati -> {iso_sana: {USD: {...}, EUR: {...}, RUB: {...}}}."""
    out: dict[str, dict] = {}
    for it in lst or []:
        if not isinstance(it, dict):
            continue
        ccy = str(it.get("Ccy", "")).upper()
        if ccy not in ("USD", "EUR", "RUB"):
            continue
        ds = str(it.get("Date", ""))  # "27.10.2026" — kurs amal qiladigan sana
        try:
            iso = datetime.strptime(ds, "%d.%m.%Y").date().isoformat()
            rate = float(it["Rate"])
        except Exception:
            continue
        out.setdefault(iso, {})[ccy] = {"rate": rate, "diff": float(it.get("Diff") or 0), "date": ds}
    return out


def save(payload: dict) -> list[str]:
    """Kelgan ro'yxatlarni sanasi bo'yicha bazaga yozadi. Saqlangan sanalar ro'yxatini qaytaradi."""
    saved = []
    now = int(time.time())
    for lst in payload.get("lists") or []:
        for iso, rates in _parse(lst).items():
            if "USD" in rates:
                db.kv_set(f"rates_push:{iso}", json.dumps({"t": now, "rates": rates}, ensure_ascii=False))
                saved.append(iso)
    if saved:
        db.kv_set("rates_push_last", str(now))
    return sorted(set(saved))


def get(iso: str, max_age: int | None = None) -> dict:
    """Shu sanaga kompyuterdan kelgan MB kursi (bo'lmasa — {})."""
    v = db.kv_get(f"rates_push:{iso}")
    if not v:
        return {}
    try:
        j = json.loads(v)
    except Exception:
        return {}
    if max_age is not None and time.time() - j.get("t", 0) > max_age:
        return {}
    return j.get("rates") or {}


def status() -> str:
    last = db.kv_get("rates_push_last")
    if not TOKEN:
        return "⚪️ Kompyuterdan kurs qabul qilish o'chiq (KURS_PUSH_TOKEN yo'q)"
    if not last:
        return "🟡 Kompyuterdan hali kurs kelmagan"
    mins = int((time.time() - int(last)) / 60)
    mark = "✅" if mins <= 90 else "⚠️"
    return f"{mark} Kompyuterdan oxirgi kurs: {mins} daqiqa oldin"


async def _push(request: web.Request) -> web.Response:
    if not TOKEN:
        return web.json_response({"ok": False, "error": "KURS_PUSH_TOKEN o'rnatilmagan"}, status=503)
    if not hmac.compare_digest(request.headers.get("X-Token", ""), TOKEN):
        return web.json_response({"ok": False, "error": "token noto'g'ri"}, status=403)
    try:
        payload = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "bad json"}, status=400)
    saved = save(payload)
    log.info("kurs push: %s", saved)
    try:  # eski (xalqaro) kursni xotiradan o'chiramiz — yangi MB kursi darhol ishlatilsin
        from . import sources
        for iso in saved:
            sources._cache.pop(f"r:{iso}", None)
    except Exception:
        pass
    return web.json_response({"ok": bool(saved), "saved": saved})


def register(app: web.Application):
    app.router.add_post(PATH, _push)
    log.info("kurs push: %s (token: %s)", PATH, bool(TOKEN))
