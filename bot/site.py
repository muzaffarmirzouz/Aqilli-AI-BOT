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


# Maqola matni tugagandan keyingi saytdagi bloklar (shulardan keyingi hamma narsa tashlab yuboriladi)
_STOP_WORDS = [
    "Теглар", "Teglar", "Улашиш", "Ulashish", "Мавзуга доир", "Mavzuga doir",
    "Ижтимоий тармоқлар", "Ijtimoiy tarmoqlar", "Энг кўп ўқилган", "Eng ko'p o'qilgan",
    "Жаҳон янгиликлари", "Jahon yangiliklari", "Муҳаррир танлови", "Muharrir tanlovi",
    "Тасодифий хабарлар", "Tasodifiy xabarlar", "Ўхшаш", "Изоҳлар", "Developed by",
]
_STOP = re.compile("|".join(re.escape(w) for w in _STOP_WORDS) + r"|©", re.I)
# element matni aynan shu so'z bilan boshlanadigan joy:  >  Теглар:
_STOP_TAG = re.compile(r">\s*(?:" + "|".join(re.escape(w) for w in _STOP_WORDS) + r"|©)", re.I)
# sana va ko'rishlar soni qatori: "2026-10-07 02:21 245"
_META_LINE = re.compile(r"^\d{4}-\d{2}-\d{2}(\s+\d{1,2}:\d{2})?(\s+\d+)?$")


def _clean(fragment: str) -> str:
    t = re.sub(r"<br\s*/?>", "\n", fragment, flags=re.I)
    t = re.sub(r"<[^>]+>", "", t)
    return re.sub(r"[ \t\xa0]+", " ", html.unescape(t)).strip()


def _ok_para(t: str) -> bool:
    return len(t) >= 25 and not _META_LINE.match(t) and not _STOP.match(t)


def parse_body(page: str) -> str:
    """Maqolaning to'liq matni: sarlavhadan (h1) keyin, «Теглар / Улашиш / Мавзуга доир…» bloklarigacha.
    Topilmasa — og:description."""
    page = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", "", page, flags=re.S | re.I)
    m = re.search(r"<h1[^>]*>.*?</h1>", page, re.I | re.S)
    tail = page[m.end():] if m else page
    cut = _STOP_TAG.search(tail)
    if cut:
        tail = tail[:cut.start()]
    paras = []
    for frag in re.findall(r"<p[^>]*>(.*?)</p>", tail, re.S | re.I):
        t = _clean(frag)
        if _ok_para(t):
            paras.append(t)
        if sum(len(x) for x in paras) > 6000:
            break
    body = "\n\n".join(paras)
    if not body:
        # <p> yo'q bo'lsa: div/br bo'yicha qatorlar
        text = re.sub(r"<(br|/div|/p|/li|/h\d)[^>]*>", "\n", tail, flags=re.I)
        text = html.unescape(re.sub(r"<[^>]+>", "", text))
        lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.split("\n")]
        body = "\n\n".join(ln for ln in lines if len(ln) >= 40 and _ok_para(ln))[:6000]
    return body or _meta(page, "og:description")


async def fetch_article(session, news_id: int):
    url = f"{SITE_URL}/news/{news_id}"
    page = await _get(session, url)
    title, images = parse_article(page)
    body = parse_body(page)
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
    return url, title, img, body


async def watcher(bot, on_new, notify_admins):
    """on_new(bot, title, link, image_bytes, body) — yangi maqola uchun chaqiriladi."""
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
                                    url, title, img, body = await fetch_article(session, nid)
                                    if title and img:
                                        await on_new(bot, title, url, img, body)
                                    else:
                                        await notify_admins(bot, f"🆕 Saytda yangi maqola, lekin "
                                                                 f"{'rasm' if title else 'sarlavha'} topilmadi:\n{url}")
                                except Exception as e:
                                    log.warning("maqola %s: %s", nid, e)
                                db.kv_set("site_last_id", str(nid))
            except Exception as e:
                log.warning("sayt kuzatuvi: %s", e)
            await asyncio.sleep(POLL_SEC)
