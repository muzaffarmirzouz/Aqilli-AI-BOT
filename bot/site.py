"""namanganliklar.uz ni kuzatish: yangi maqola chiqsa — adminlarga tayyor yangilik rasmi yuboriladi.

Kanalga avtomatik joylanmaydi: admin «📤 Kanalga yuborish» ni bosgandagina chiqadi.
"""
import asyncio
import html
import logging
import os
import re

import aiohttp

from . import db

log = logging.getLogger(__name__)

SITE_URL = os.getenv("SITE_URL", "https://namanganliklar.uz").rstrip("/")
POLL_SEC = int(os.getenv("SITE_POLL_SEC", "90"))
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; NamanganliklarBot/1.0)"}
_TIMEOUT = aiohttp.ClientTimeout(total=25)
_ID_RE = re.compile(r"/news/(\d+)")


def enabled() -> bool:
    return bool(SITE_URL) and (db.kv_get("site_watch") or "on") == "on"


async def _get(session: aiohttp.ClientSession, url: str, binary: bool = False):
    async with session.get(url) as r:
        r.raise_for_status()
        return await (r.read() if binary else r.text())


def _meta(page: str, prop: str) -> str:
    m = re.search(rf'<meta[^>]+(?:property|name)=["\']{prop}["\'][^>]*content=["\']([^"\']*)["\']', page, re.I) or \
        re.search(rf'<meta[^>]+content=["\']([^"\']*)["\'][^>]*(?:property|name)=["\']{prop}["\']', page, re.I)
    return html.unescape(m.group(1)).strip() if m else ""


def parse_article(page: str) -> tuple[str, str]:
    """Sahifadan sarlavha va eng katta rasm manzilini oladi."""
    title = _meta(page, "og:title") or (re.search(r"<title>(.*?)</title>", page, re.S | re.I) or [None, ""])[1]
    title = re.sub(r"\s*[-|–]\s*Namanganliklar\.uz\s*$", "", html.unescape(title).strip(), flags=re.I)
    image = _meta(page, "og:image")
    # Xuddi shu rasmning kattaroq o'lchamdagi nusxasi sahifada bo'lsa — o'shani olamiz
    m = re.search(r"/uploads/\d+x\d+/([0-9a-f]{16,}\.\w+)", image or "")
    if m:
        best, area = image, 0
        for w, h in re.findall(rf"/uploads/(\d+)x(\d+)/{re.escape(m.group(1))}", page):
            if int(w) * int(h) > area:
                area = int(w) * int(h)
                best = f"{SITE_URL}/uploads/{w}x{h}/{m.group(1)}"
        image = best
        # Railway'da sozlanadigan katta o'lcham (masalan SITE_IMAGE_SIZE=1200x800)
        big = os.getenv("SITE_IMAGE_SIZE", "800x450").strip()
        if big:
            image = [f"{SITE_URL}/uploads/{big}/{m.group(1)}", image]
    if isinstance(image, str):
        image = [image] if image else []
    return title, image


async def fetch_article(session, news_id: int):
    url = f"{SITE_URL}/news/{news_id}"
    page = await _get(session, url)
    title, images = parse_article(page)
    img = None
    for u in images:
        if u.startswith("/"):
            u = SITE_URL + u
        try:
            img = await _get(session, u, binary=True)
            if img:
                break
        except Exception:
            continue
    return url, title, img


async def watcher(bot, on_new, notify_admins):
    """on_new(bot, title, link, image_bytes) — yangi maqola uchun chaqiriladi."""
    if not SITE_URL:
        return
    await asyncio.sleep(20)
    async with aiohttp.ClientSession(timeout=_TIMEOUT, headers=_HEADERS) as session:
        while True:
            try:
                if enabled():
                    home = await _get(session, SITE_URL + "/")
                    ids = sorted({int(x) for x in _ID_RE.findall(home)})
                    last = db.kv_get("site_last_id")
                    if ids:
                        if last is None:
                            # birinchi ishga tushish: eski maqolalarni yubormaymiz
                            db.kv_set("site_last_id", str(ids[-1]))
                        else:
                            new = [i for i in ids if i > int(last)][-5:]
                            for nid in new:
                                try:
                                    url, title, img = await fetch_article(session, nid)
                                    if title and img:
                                        await on_new(bot, title, url, img)
                                    else:
                                        await notify_admins(bot, f"🆕 Saytda yangi maqola, lekin "
                                                                 f"{'rasm' if title else 'sarlavha'} topilmadi:\n{url}")
                                except Exception as e:
                                    log.warning("maqola %s: %s", nid, e)
                                db.kv_set("site_last_id", str(nid))
            except Exception as e:
                log.warning("sayt kuzatuvi: %s", e)
            await asyncio.sleep(POLL_SEC)
