"""▶️ YouTube izohlariga avtomatik (AI) javob — bot/igflow.py bilan bir xil mantiq.

YouTube izohni webhook bilan yubormaydi, shuning uchun bot kanal izohlarini har 3 daqiqada tekshiradi.
Yangi izohga: kalit so'zli qoida (igflow qoidalari, faqat «izohga javob» matni) → AI (Claude) → bo'lmasa javob yo'q.

Railway o'zgaruvchilari:
  YTFLOW_CLIENT_ID, YTFLOW_CLIENT_SECRET, YTFLOW_REFRESH_TOKEN — Google OAuth (YouTube Data API v3)
  YTFLOW_POLL_SEC  — tekshirish oralig'i, soniya (standart 180)
  IGFLOW_AI_KEY    — Anthropic kaliti (Instagram bilan umumiy)
Telegram (adminlar): /yt, /yt on|off, /yt_qoida, /yt_limit, /yt_test.
Kvota: YouTube kuniga 10 000 birlik beradi; javob yozish 50 birlik, o'qish 1 birlik.
"""
import asyncio
import html
import json
import logging
import os
import re
import time

import aiohttp
from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from . import db, igflow
from .config import ADMIN_IDS

log = logging.getLogger(__name__)

CLIENT_ID = os.getenv("YTFLOW_CLIENT_ID", "").strip()
CLIENT_SECRET = os.getenv("YTFLOW_CLIENT_SECRET", "").strip()
REFRESH_TOKEN = os.getenv("YTFLOW_REFRESH_TOKEN", "").strip()
POLL = max(60, int(os.getenv("YTFLOW_POLL_SEC", "180") or 180))
API = "https://www.googleapis.com/youtube/v3"
_TIMEOUT = aiohttp.ClientTimeout(total=25)
KV_CFG = "ytflow_cfg"
KV_SEEN = "ytflow_seen"
HARD_DAILY = 150            # kvotadan oshib ketmaslik uchun qat'iy chegara (150 × 50 = 7500 birlik)
PER_AUTHOR_HOUR = 3

_token = {"v": "", "exp": 0.0}
_channel = {"id": ""}
_bot = None
_author_rate: dict[str, list[float]] = {}
_blocked_until = [0.0]      # kvota tugasa — ertaga 00:00 gacha to'xtaydi


def configured() -> bool:
    return bool(CLIENT_ID and CLIENT_SECRET and REFRESH_TOKEN)


def _cfg() -> dict:
    try:
        c = json.loads(db.kv_get(KV_CFG) or "{}")
    except Exception:
        c = {}
    return {"on": bool(c.get("on")), "prompt": c.get("prompt") or "", "limit": int(c.get("limit") or 100)}


def _save(c: dict):
    db.kv_set(KV_CFG, json.dumps(c, ensure_ascii=False))


def _day() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(time.time() + 5 * 3600))


def _used() -> int:
    try:
        st = json.loads(db.kv_get("ytflow_day") or "{}")
    except Exception:
        return 0
    return int(st.get("n", 0)) if st.get("d") == _day() else 0


def _take() -> bool:
    c = _cfg()
    n = _used()
    if n >= min(c["limit"], HARD_DAILY):
        return False
    db.kv_set("ytflow_day", json.dumps({"d": _day(), "n": n + 1}))
    return True


# ---------- Google API ----------

class YtError(Exception):
    def __init__(self, msg, status=0, reason=""):
        super().__init__(msg)
        self.status, self.reason = status, reason


async def _access_token(session) -> str:
    if _token["v"] and time.time() < _token["exp"] - 60:
        return _token["v"]
    async with session.post("https://oauth2.googleapis.com/token", data={
        "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
        "refresh_token": REFRESH_TOKEN, "grant_type": "refresh_token"}) as r:
        d = await r.json(content_type=None)
    if not isinstance(d, dict) or "access_token" not in d:
        raise YtError(f"Google token olinmadi: {(d or {}).get('error')} — {(d or {}).get('error_description') or d}")
    _token["v"], _token["exp"] = d["access_token"], time.time() + int(d.get("expires_in", 3600))
    return _token["v"]


async def _api(method: str, path: str, params=None, body=None):
    async with aiohttp.ClientSession(timeout=_TIMEOUT) as s:
        tok = await _access_token(s)
        async with s.request(method, f"{API}/{path}", params=params, json=body,
                             headers={"Authorization": f"Bearer {tok}"}) as r:
            d = await r.json(content_type=None)
            status = r.status
    if isinstance(d, dict) and d.get("error"):
        e = d["error"]
        reason = ((e.get("errors") or [{}])[0]).get("reason", "")
        raise YtError(f"{e.get('message')} ({reason or status})", status, reason)
    return d


async def _my_channel() -> str:
    if not _channel["id"]:
        d = await _api("GET", "channels", {"part": "id", "mine": "true"})
        items = d.get("items") or []
        if not items:
            raise YtError("Kanal topilmadi (OAuth qilingan akkauntda YouTube kanal yo'q)")
        _channel["id"] = items[0]["id"]
    return _channel["id"]


# ---------- javob mantiqi ----------

def _text_of(top: dict) -> str:
    sn = top.get("snippet") or {}
    t = sn.get("textOriginal") or re.sub(r"<[^>]+>", "", sn.get("textDisplay") or "")
    return html.unescape(t).strip()


def _author_ok(author: str) -> bool:
    now = time.time()
    lst = [t for t in _author_rate.get(author, []) if now - t < 3600]
    if len(lst) >= PER_AUTHOR_HOUR:
        _author_rate[author] = lst
        return False
    lst.append(now)
    _author_rate[author] = lst
    return True


async def _make_reply(text: str, author: str, video_id: str) -> str:
    """Qoida → AI → umumiy javob (/izoh_avto). Qaytaradi javob matni yoki "" (javob yozilmaydi)."""
    c = _cfg()
    if not c["on"] or _used() >= min(c["limit"], HARD_DAILY):
        return ""
    rule = igflow.match_rule(text, igflow._rules(), media_id="", dm=True)
    if rule and rule.get("reply"):
        return igflow._pick(rule["reply"])
    fallback = igflow._pick(igflow._default_reply())      # Instagramdagi «har bir izohga javob» matnlari
    if not igflow._ai_key():
        return fallback
    try:
        out = await igflow._claude(
            igflow._ai_system(c["prompt"], "", "comment", platform="Namanganliklar.uz YouTube kanali"),
            [{"role": "user", "content": f"<xabar>{text[:500]}</xabar>"}], 250)
    except Exception as e:
        log.warning("ytflow: AI: %s", e)
        return fallback
    if "[ODAM]" in out:                       # AI bilmaydigan savol: umumiy «rahmat» yozmaymiz, adminga aytamiz
        await igflow._tell_admins(f"🙋 YouTube izohi — AI javob bera olmadi, odam kerak.\n"
                                  f"{author}: {text[:300]}\nhttps://youtu.be/{video_id}")
        return ""
    if not out or "[SKIP]" in out:
        return fallback
    return out.strip().strip('"«»')[:900]


async def process(threads: list[dict]) -> int:
    """Yangi izoh oqimlarini (eskisidan yangisiga) qayta ishlaydi. Qaytaradi: yozilgan javoblar soni."""
    try:
        seen = json.loads(db.kv_get(KV_SEEN) or "null")
    except Exception:
        seen = None
    first_run = seen is None
    seen = list(seen or [])
    seen_set = set(seen)
    me = await _my_channel()
    sent = 0
    for th in threads:
        tid = th.get("id")
        if not tid or tid in seen_set:
            continue
        seen.append(tid)
        seen_set.add(tid)
        if first_run:                       # birinchi ishga tushish: eski izohlarga javob bermaymiz
            continue
        top = (th.get("snippet") or {}).get("topLevelComment") or {}
        sn = top.get("snippet") or {}
        author_ch = ((sn.get("authorChannelId") or {}).get("value")) or ""
        if author_ch == me or (th.get("snippet") or {}).get("totalReplyCount", 0) > 0:
            continue
        text = _text_of(top)
        if not text or not _author_ok(author_ch or sn.get("authorDisplayName", "")):
            continue
        reply = await _make_reply(text, sn.get("authorDisplayName", ""), sn.get("videoId", ""))
        if not reply:
            continue
        if not _take():                     # kunlik limit (kvota) — har bir yozilgan javob hisoblanadi
            continue
        await asyncio.sleep(2)
        try:
            await _api("POST", "comments", {"part": "snippet"},
                       {"snippet": {"parentId": top.get("id") or tid, "textOriginal": reply}})
            sent += 1
        except YtError as e:
            log.warning("ytflow: javob yozilmadi: %s", e)
            if e.reason in ("quotaExceeded", "dailyLimitExceeded"):
                _blocked_until[0] = time.time() + 6 * 3600
                await igflow._alert("⚠️ YouTube kunlik kvotasi tugadi — keyinroq qayta urinadi.")
                break
            await igflow._alert(f"⚠️ YouTube izohiga javob yozilmadi: {html.escape(str(e))[:200]}")
    db.kv_set(KV_SEEN, json.dumps(seen[-1000:]))
    return sent


async def poll_once() -> int:
    if time.time() < _blocked_until[0]:
        return 0
    cid = await _my_channel()
    d = await _api("GET", "commentThreads", {
        "part": "snippet", "allThreadsRelatedToChannelId": cid, "order": "time", "maxResults": 50})
    items = list(reversed(d.get("items") or []))
    return await process(items)


async def watcher():
    """Doimiy tekshiruv sikli (main.py'da fon vazifasi sifatida ishga tushadi)."""
    if not configured():
        log.info("ytflow: sozlanmagan (YTFLOW_* o'zgaruvchilari yo'q) — ishga tushmadi")
        return
    await asyncio.sleep(45)
    while True:
        try:
            await poll_once()
        except Exception as e:
            log.warning("ytflow: tekshiruv: %s", e)
            if isinstance(e, YtError) and e.status in (401, 403) and e.reason not in ("quotaExceeded", "rateLimitExceeded"):
                await igflow._alert(f"⚠️ YouTube ulanishida xato: {html.escape(str(e))[:200]}")
        await asyncio.sleep(POLL)


# ---------- Telegram buyruqlari (faqat adminlar) ----------

router = Router()
router.message.filter(F.from_user.id.in_(ADMIN_IDS), F.chat.type == ChatType.PRIVATE)


@router.message(Command("yt"))
async def cmd_yt(m: Message, command: CommandObject):
    arg = (command.args or "").strip().lower()
    c = _cfg()
    if arg in ("on", "off"):
        if arg == "on" and not (configured() and igflow._ai_key()):
            await m.answer("❌ Avval Railway'da YTFLOW_CLIENT_ID, YTFLOW_CLIENT_SECRET, YTFLOW_REFRESH_TOKEN va "
                           "IGFLOW_AI_KEY o'zgaruvchilarini qo'ying.")
            return
        c["on"] = arg == "on"
        _save(c)
        await m.answer("✅ YouTube AI javob yoqildi." if c["on"] else "YouTube AI javob o'chirildi.")
        return
    await m.answer(
        f"▶️ <b>YouTube AI javob:</b> {'yoqilgan' if c['on'] else 'o‘chiq'}\n"
        f"🔑 Google ulanishi: {'bor' if configured() else 'YO‘Q'} · AI kaliti: {'bor' if igflow._ai_key() else 'YO‘Q'}\n"
        f"📝 Ko'rsatma: {html.escape(c['prompt'][:200]) or '—'}\n"
        f"Kunlik limit: {min(c['limit'], HARD_DAILY)} (bugun: {_used()}) · tekshiruv: har {POLL} soniyada\n\n"
        "/yt on | /yt off\n/yt_qoida ko'rsatma matni\n/yt_limit 100\n/yt_test savol\n"
        "Kalit so'zli qoidalar Instagram bilan umumiy (/qoida).")


@router.message(Command("yt_qoida"))
async def cmd_yt_prompt(m: Message, command: CommandObject):
    arg = (command.args or "").strip()
    if not arg:
        await m.answer("Format: <code>/yt_qoida Ko'rsatma matni</code> (o'chirish: <code>/yt_qoida off</code>)")
        return
    c = _cfg()
    c["prompt"] = "" if arg.lower() == "off" else arg[:3000]
    _save(c)
    await m.answer("✅ YouTube ko'rsatmasi saqlandi.")


@router.message(Command("yt_limit"))
async def cmd_yt_limit(m: Message, command: CommandObject):
    arg = (command.args or "").strip()
    if not arg.isdigit():
        await m.answer(f"Hozirgi limit: {min(_cfg()['limit'], HARD_DAILY)} ta/kun (bugun: {_used()}). "
                       f"O'zgartirish: <code>/yt_limit 100</code> (ko'pi bilan {HARD_DAILY} — YouTube kvotasi tufayli)")
        return
    c = _cfg()
    c["limit"] = int(arg)
    _save(c)
    await m.answer(f"✅ Kunlik limit: {min(int(arg), HARD_DAILY)} ta.")


@router.message(Command("yt_test"))
async def cmd_yt_test(m: Message, command: CommandObject):
    arg = (command.args or "").strip()
    if not arg or not igflow._ai_key():
        await m.answer("Format: <code>/yt_test Videoning narxi qancha?</code> (IGFLOW_AI_KEY kerak)")
        return
    try:
        out = await igflow._claude(
            igflow._ai_system(_cfg()["prompt"], "", "comment", platform="Namanganliklar.uz YouTube kanali"),
            [{"role": "user", "content": f"<xabar>{arg[:500]}</xabar>"}], 250)
    except Exception as e:
        await m.answer(f"❌ {html.escape(str(e))[:300]}")
        return
    note = {"[ODAM]": "🙋 AI javob bera olmadi (ko'rsatmada ma'lumot yo'q) — haqiqiy izohda sizga xabar keladi.",
            "[SKIP]": "⏭ Bu izohga AI javob yozmaydi."}
    await m.answer(note.get(out.strip(), "▶️ " + html.escape(out or "—")))


def setup(bot):
    global _bot
    _bot = bot


@router.message(Command("yt_tekshir"))
async def cmd_yt_check(m: Message):
    """Diagnostika: kanalning oxirgi izohlarini ko'rsatadi va hozir bir marta tekshiradi."""
    if not configured():
        await m.answer("❌ YTFLOW_* o'zgaruvchilari yo'q.")
        return
    def _shape(v: str, pre: str = "") -> str:
        """Maxfiy qiymatni oshkor qilmasdan shaklini ko'rsatadi."""
        ok = "✅" if (not pre or v.startswith(pre)) else "❌"
        return f"{ok} uzunligi {len(v)}" + (", bo'sh joy/qator bor ❌" if re.search(r"\s|[\"']", v) else "")
    cfg_lines = [
        "🔎 <b>Sozlama tekshiruvi</b> (qiymatlar ko'rsatilmaydi)",
        f"Client ID: {_shape(CLIENT_ID)}; .apps.googleusercontent.com bilan tugaydi: "
        f"{'✅' if CLIENT_ID.endswith('.apps.googleusercontent.com') else '❌'}; boshi: {html.escape(CLIENT_ID[:12])}…",
        f"Client secret: {_shape(CLIENT_SECRET, 'GOCSPX-')} (odatda GOCSPX- bilan boshlanib 35 belgi)",
        f"Refresh token: {_shape(REFRESH_TOKEN, '1//')} (odatda 1// bilan boshlanadi)",
        ""]
    await m.answer("\n".join(cfg_lines))
    try:
        cid = await _my_channel()
        d = await _api("GET", "commentThreads", {
            "part": "snippet", "allThreadsRelatedToChannelId": cid, "order": "time", "maxResults": 5})
        items = d.get("items") or []
        try:
            seen = set(json.loads(db.kv_get(KV_SEEN) or "[]"))
        except Exception:
            seen = set()
        lines = [f"Kanal: <code>{html.escape(cid)}</code>", f"API qaytargan izoh oqimlari: {len(items)} (oxirgi 5 tasi)"]
        for th in items:
            top = (th.get("snippet") or {}).get("topLevelComment") or {}
            sn = top.get("snippet") or {}
            mark = "ko'rilgan" if th.get("id") in seen else "YANGI"
            lines.append(f"• [{mark}] {html.escape(sn.get('authorDisplayName', '?'))}: {html.escape(_text_of(top)[:60])} "
                         f"(javoblar: {(th.get('snippet') or {}).get('totalReplyCount', 0)})")
        n = await process(list(reversed(items)))
        lines.append(f"\nHozir tekshirildi, yozilgan javob: {n}")
        await m.answer("\n".join(lines))
    except Exception as e:
        await m.answer(f"❌ Xato: {html.escape(str(e))[:500]}")

