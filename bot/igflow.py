"""📩 Instagram avtomatlashtirish (Manychat o'rniga, o'z akkauntlar uchun).

Nima qiladi:
  • Postga yozilgan izohda kalit so'z bo'lsa — izohga ochiq javob + yozgan odamga DM (comment-to-DM);
  • DM'ga kalit so'z bo'yicha avtojavob, kalit so'z topilmasa — umumiy avtojavob (ixtiyoriy);
  • (ixtiyoriy) kalit so'zi bo'lmagan HAR BIR izohga ham ochiq javob — /izoh_avto (variantlar «||» bilan).

Railway o'zgaruvchilari (mavjud META_* o'zgaruvchilariga TEGMAYDI — avto-post eskicha ishlaydi):
  IGFLOW_TOKEN         — Instagram API (Instagram login) tokeni: Meta → Сценарии использования →
                         «Настройка API для входа в Instagram» → «Сгенерировать маркер»
  IGFLOW_TOKEN_2, _3   — (ixtiyoriy) 2- va 3-akkaunt tokenlari
  IGFLOW_APP_SECRET    — «Секрет приложения Instagram» (webhook imzosini tekshirish uchun)
  IGFLOW_VERIFY_TOKEN  — o'zingiz o'ylab topgan matn; Meta'dagi «Verify token» maydoniga ham shuni yozasiz
  IGFLOW_COOLDOWN_MIN  — (ixtiyoriy) bir odamga bir qoida bo'yicha qayta javob berish oralig'i, daqiqada (standart 60)
  IGFLOW_COMMENT_COOLDOWN_MIN — (ixtiyoriy) /izoh_avto: bir odamga qayta ochiq javob berish oralig'i (standart 360)
  PORT                 — Railway o'zi beradi

Webhook manzili: https://<domen>/ig/webhook  (veb-serverni yandex/skill.py ishga tushiradi, shu yerga ulanadi)
Telegram buyruqlari (faqat adminlarga): /igflow, /qoida, /qoida_qosh, /qoida_ochir, /dm_avto
"""
import asyncio
import hashlib
import hmac
import html
import json
import logging
import os
import random
import re
import time
from collections import OrderedDict

import aiohttp
from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from aiohttp import web

from . import db
from .config import ADMIN_IDS

log = logging.getLogger(__name__)

VER = os.getenv("META_API_VERSION", "v21.0").strip()
BASE = f"https://graph.instagram.com/{VER}"
PATH = "/ig/webhook"
APP_SECRET = os.getenv("IGFLOW_APP_SECRET", "").strip()
VERIFY_TOKEN = os.getenv("IGFLOW_VERIFY_TOKEN", "").strip()
COOLDOWN = int(os.getenv("IGFLOW_COOLDOWN_MIN", "60") or 60) * 60
DEFAULT_DM_COOLDOWN = 12 * 3600  # umumiy avtojavob: bir odamga 12 soatda bir marta
# Har bir izohga umumiy ochiq javob: bir odamga qayta javob berish oralig'i (standart 6 soat)
COMMENT_COOLDOWN = int(os.getenv("IGFLOW_COMMENT_COOLDOWN_MIN", "360") or 360) * 60
_TIMEOUT = aiohttp.ClientTimeout(total=30)
_SEEN_LIMIT = 5000
_ALERT_EVERY = 1800

KV_RULES = "igflow_rules"
KV_DEFAULT_DM = "igflow_default_dm"
KV_DEFAULT_REPLY = "igflow_default_reply"

_bot = None
_tasks: set[asyncio.Task] = set()
_seen: "OrderedDict[str, int]" = OrderedDict()   # qayta yetkazilgan hodisalardan himoya
_cooldown: dict[str, float] = {}                 # (akkaunt, odam, qoida) -> oxirgi javob vaqti
_acc_token: dict[str, str] = {}                  # ig_id -> token
_acc_name: dict[str, str] = {}                   # ig_id -> username
_last_alert = 0.0


class IgError(Exception):
    pass


# ---------- tokenlar ----------

_ENV_TOKENS = ("IGFLOW_TOKEN", "IGFLOW_TOKEN_2", "IGFLOW_TOKEN_3")
REFRESH_EVERY = 7 * 86400      # token har 7 kunda yangilanadi (Instagram tokeni 60 kun yashaydi)


def _tok_state(name: str) -> dict:
    try:
        return json.loads(db.kv_get(f"igflow_tok_{name}") or "{}")
    except Exception:
        return {}


def _tokens() -> list[str]:
    """Railway'dagi tokenlar; avtomatik yangilangan bo'lsa — yangisi (env'dagi token o'zgarmagan bo'lsa)."""
    out = []
    for name in _ENV_TOKENS:
        v = os.getenv(name, "").strip()
        if not v:
            continue
        st = _tok_state(name)
        if st.get("src") == hashlib.sha256(v.encode()).hexdigest() and st.get("tok"):
            v = st["tok"]
        out.append(v)
    return out


async def refresh_tokens(force: bool = False) -> tuple[int, list[str]]:
    """Muddati yaqinlashgan tokenlarni yangilaydi. Qaytaradi: (yangilanganlar soni, xatolar)."""
    done, errs = 0, []
    async with aiohttp.ClientSession(timeout=_TIMEOUT) as s:
        for name in _ENV_TOKENS:
            env = os.getenv(name, "").strip()
            if not env:
                continue
            src = hashlib.sha256(env.encode()).hexdigest()
            st = _tok_state(name)
            if st.get("src") != src:
                st = {"src": src, "tok": env, "at": 0}
            if not force and time.time() - float(st.get("at") or 0) < REFRESH_EVERY:
                continue
            try:
                async with s.get("https://graph.instagram.com/refresh_access_token",
                                 params={"grant_type": "ig_refresh_token", "access_token": st["tok"]}) as r:
                    data = await r.json(content_type=None)
                new = data.get("access_token") if isinstance(data, dict) else None
                if not new:
                    err = (data or {}).get("error", {}) if isinstance(data, dict) else {}
                    raise IgError(f"{err.get('message') or data} (kod {err.get('code')})")
            except Exception as e:
                errs.append(f"{name}: {e}")
                log.warning("igflow: %s yangilanmadi: %s", name, e)
                continue
            db.kv_set(f"igflow_tok_{name}", json.dumps({"src": src, "tok": new, "at": time.time()}))
            done += 1
    if done:
        _acc_token.clear()   # ig_id -> token xaritasi qayta tuziladi
    return done, errs


async def _refresher():
    """Har 12 soatda tekshiradi; xato bo'lsa adminga xabar beradi."""
    await asyncio.sleep(60)
    while True:
        try:
            _, errs = await refresh_tokens()
            if errs:
                await _alert("⚠️ Instagram tokenini yangilab bo'lmadi:\n" + "\n".join(errs[:3]) +
                             "\nToken muddati tugamasdan Railway'da IGFLOW_TOKEN'ni qo'lda yangilang.")
        except Exception:
            log.exception("igflow: token yangilash")
        await asyncio.sleep(12 * 3600)


async def _graph(session, method: str, path: str, token: str, **kw):
    kw.setdefault("params", {})["access_token"] = token
    async with session.request(method, f"{BASE}/{path}", **kw) as r:
        data = await r.json(content_type=None)
    if isinstance(data, dict) and data.get("error"):
        err = data["error"]
        raise IgError(f"{err.get('message')} (kod {err.get('code')})")
    return data


async def _whoami(session, token: str) -> tuple[str, str]:
    me = await _graph(session, "GET", "me", token, params={"fields": "user_id,username"})
    return str(me.get("user_id") or me.get("id")), str(me.get("username") or "")


async def _token_for(session, ig_id: str) -> str:
    """Webhook'dagi akkaunt ID'siga mos tokenni topadi (tokenlar birinchi marta so'ralganda tanib olinadi)."""
    if ig_id in _acc_token:
        return _acc_token[ig_id]
    toks = _tokens()
    for t in toks:
        try:
            uid, name = await _whoami(session, t)
        except Exception as e:
            log.warning("igflow: tokenni tanib bo'lmadi: %s", e)
            continue
        _acc_token[uid] = t
        _acc_name[uid] = name
    if ig_id in _acc_token:
        return _acc_token[ig_id]
    if len(toks) == 1:   # bitta akkaunt — shubha yo'q
        return toks[0]
    raise IgError(f"ID {ig_id} akkaunt uchun token topilmadi (IGFLOW_TOKEN* ni tekshiring)")


# ---------- qoidalar ----------

def _rules() -> list[dict]:
    try:
        return json.loads(db.kv_get(KV_RULES) or "[]")
    except Exception:
        return []


def _save_rules(rules: list[dict]):
    db.kv_set(KV_RULES, json.dumps(rules, ensure_ascii=False))


def _default_dm() -> str:
    return (db.kv_get(KV_DEFAULT_DM) or "").strip()


def _default_reply() -> str:
    """Kalit so'zi bo'lmagan izohlarga ochiq javob. Bir nechta variant «||» bilan ajratiladi."""
    return (db.kv_get(KV_DEFAULT_REPLY) or "").strip()


def _pick(text: str) -> str:
    """«A || B || C» ko'rinishidagi matndan tasodifiy bittasini tanlaydi (bir xil matn takrorlanmasligi uchun)."""
    variants = [v.strip() for v in text.split("||") if v.strip()]
    return random.choice(variants) if variants else ""


def match_rule(text: str, rules: list[dict], media_id: str = "", dm: bool = False) -> dict | None:
    """Matndagi kalit so'zga mos qoidani topadi.
    Bitta postga bog'langan qoidalar (rule["media"]) faqat o'sha postdagi izohlarga ishlaydi va umumiy
    qoidalardan ustun turadi; DM'larda ular e'tiborga olinmaydi."""
    t = (text or "").lower()
    if not t:
        return None
    for bound in (True, False):
        for r in rules:
            media = r.get("media") or []
            if bool(media) != bound:
                continue
            if bound and (dm or str(media_id) not in media):
                continue
            for kw in r.get("kw", []):
                if kw and kw in t:
                    return r
    return None


_POST_RE = re.compile(r"instagram\.com/(?:[\w.]+/)?(?:p|reels?|tv)/([A-Za-z0-9_-]+)")


async def resolve_post(url: str) -> tuple[str, str]:
    """Instagram post havolasidan media ID topadi: (media_id, permalink). Akkauntlarning oxirgi ~500 ta posti ichidan qidiradi."""
    m = _POST_RE.search(url or "")
    if not m:
        raise IgError("Post havolasi noto'g'ri. Namuna: https://www.instagram.com/p/ABC123/")
    code = m.group(1)
    toks = _tokens()
    if not toks:
        raise IgError("IGFLOW_TOKEN o'rnatilmagan")
    async with aiohttp.ClientSession(timeout=_TIMEOUT) as s:
        for t in toks:
            after = ""
            for _ in range(10):
                params = {"fields": "id,permalink", "limit": "50"}
                if after:
                    params["after"] = after
                try:
                    data = await _graph(s, "GET", "me/media", t, params=params)
                except IgError as e:
                    log.warning("igflow: postlar ro'yxati: %s", e)
                    break
                for it in data.get("data") or []:
                    link = it.get("permalink") or ""
                    if f"/{code}/" in link or link.rstrip("/").endswith(f"/{code}"):
                        return str(it["id"]), link or url
                paging = data.get("paging") or {}
                after = (paging.get("cursors") or {}).get("after") or ""
                if not after or not paging.get("next"):
                    break
    raise IgError("Post topilmadi (akkauntlaringizning oxirgi ~500 ta postidan qidirildi). Havolani tekshiring.")


# ---------- yordamchilar ----------

def _bg(coro):
    t = asyncio.create_task(coro)
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)


def _first_time(key: str) -> bool:
    if key in _seen:
        return False
    _seen[key] = int(time.time())
    while len(_seen) > _SEEN_LIMIT:
        _seen.popitem(last=False)
    return True


def _cooldown_ok(key: str, seconds: int) -> bool:
    now = time.time()
    last = _cooldown.get(key, 0)
    if now - last < seconds:
        return False
    _cooldown[key] = now
    if len(_cooldown) > 20000:   # eskilarini tozalaymiz
        for k in [k for k, v in _cooldown.items() if now - v > 86400]:
            _cooldown.pop(k, None)
    return True


async def _alert(text: str):
    """Adminlarga xato haqida xabar (yarim soatda ko'pi bilan bir marta)."""
    global _last_alert
    if not _bot or time.time() - _last_alert < _ALERT_EVERY:
        return
    _last_alert = time.time()
    for a in ADMIN_IDS:
        try:
            await _bot.send_message(a, text)
        except Exception:
            pass


# ---------- Instagram'ga yuborish ----------

async def _reply_comment(session, token: str, comment_id: str, text: str):
    await _graph(session, "POST", f"{comment_id}/replies", token, data={"message": text})


async def _private_reply(session, token: str, ig_id: str, comment_id: str, text: str):
    """Izoh egasiga DM (private reply) — har bir izohga bir marta, izohdan keyin 7 kun ichida."""
    await _graph(session, "POST", f"{ig_id}/messages", token,
                 json={"recipient": {"comment_id": comment_id}, "message": {"text": text}})


async def _send_dm(session, token: str, ig_id: str, user_id: str, text: str):
    await _graph(session, "POST", f"{ig_id}/messages", token,
                 json={"recipient": {"id": user_id}, "message": {"text": text}})


# ---------- hodisalarni qayta ishlash ----------

async def _on_comment(ig_id: str, v: dict):
    cid = str(v.get("id") or "")
    text = v.get("text") or ""
    frm = v.get("from") or {}
    uid = str(frm.get("id") or "")
    if not cid or v.get("parent_id"):          # izohga javoblarga (shu jumladan o'zimiznikiga) tegmaymiz
        return
    if uid == ig_id:                            # o'z izohimiz
        return
    media_id = str((v.get("media") or {}).get("id") or "")
    rule = match_rule(text, _rules(), media_id)
    default = _default_reply()
    if not rule and not default:
        return
    if not _first_time(f"c:{cid}"):
        return
    # Ochiq javob: qoidadagi matn; qoidada u bo'lmasa yoki qoida mos kelmasa — umumiy javob (bir odamga cooldown bilan)
    reply = (rule or {}).get("reply") or ""
    if not reply and default and _cooldown_ok(f"{ig_id}:{uid}:creply", COMMENT_COOLDOWN):
        reply = _pick(default)
    send_dm = bool(rule and rule.get("dm")) and _cooldown_ok(f"{ig_id}:{uid}:{rule['id']}", COOLDOWN)
    if not reply and not send_dm:
        return
    async with aiohttp.ClientSession(timeout=_TIMEOUT) as s:
        token = await _token_for(s, ig_id)
        if reply:
            try:
                await _reply_comment(s, token, cid, reply)
            except Exception as e:
                log.warning("igflow: izohga javob: %s", e)
                await _alert(f"⚠️ Instagram izohiga javob bermadi: {html.escape(str(e))[:200]}")
        if send_dm:
            try:
                await _private_reply(s, token, ig_id, cid, rule["dm"])
                log.info("igflow: izoh→DM yuborildi (qoida %s, @%s)", rule["id"], frm.get("username"))
            except Exception as e:
                log.warning("igflow: DM: %s", e)
                await _alert(f"⚠️ Instagram DM yuborilmadi: {html.escape(str(e))[:200]}")


async def _on_dm(ig_id: str, ev: dict):
    msg = ev.get("message") or {}
    sender = str((ev.get("sender") or {}).get("id") or "")
    mid = str(msg.get("mid") or "")
    text = msg.get("text") or ""
    if not msg or msg.get("is_echo") or not sender or sender == ig_id or not text:
        return
    if mid and not _first_time(f"m:{mid}"):
        return
    rule = match_rule(text, _rules(), dm=True)
    if rule and rule.get("dm"):
        reply, ckey, secs = rule["dm"], f"{ig_id}:{sender}:{rule['id']}", COOLDOWN
    elif _default_dm():
        reply, ckey, secs = _default_dm(), f"{ig_id}:{sender}:default", DEFAULT_DM_COOLDOWN
    else:
        return
    if not _cooldown_ok(ckey, secs):
        return
    async with aiohttp.ClientSession(timeout=_TIMEOUT) as s:
        try:
            token = await _token_for(s, ig_id)
            await _send_dm(s, token, ig_id, sender, reply)
        except Exception as e:
            log.warning("igflow: DM javob: %s", e)
            await _alert(f"⚠️ Instagram DM'ga javob bermadi: {html.escape(str(e))[:200]}")


async def handle_payload(payload: dict):
    if payload.get("object") != "instagram":
        return
    for entry in payload.get("entry") or []:
        ig_id = str(entry.get("id") or "")
        for ch in entry.get("changes") or []:
            if ch.get("field") == "comments":
                try:
                    await _on_comment(ig_id, ch.get("value") or {})
                except Exception:
                    log.exception("igflow: izoh hodisasi")
        for ev in entry.get("messaging") or []:
            try:
                await _on_dm(ig_id, ev)
            except Exception:
                log.exception("igflow: DM hodisasi")


# ---------- webhook (aiohttp) ----------

def _signature_ok(raw: bytes, header: str) -> bool:
    if not APP_SECRET:
        return False
    exp = "sha256=" + hmac.new(APP_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(header.encode(), exp.encode())


async def _verify(request: web.Request) -> web.Response:
    """Meta webhook'ni ulaganda shu manzilga GET so'rov yuboradi."""
    q = request.query
    ok = (q.get("hub.mode") == "subscribe" and bool(VERIFY_TOKEN)
          and hmac.compare_digest(q.get("hub.verify_token", "").encode(), VERIFY_TOKEN.encode()))
    if ok:
        return web.Response(text=q.get("hub.challenge", ""))
    return web.Response(status=403, text="forbidden")


async def _receive(request: web.Request) -> web.Response:
    raw = await request.read()
    if not APP_SECRET:
        return web.Response(status=503, text="IGFLOW_APP_SECRET o'rnatilmagan")
    if not _signature_ok(raw, request.headers.get("X-Hub-Signature-256", "")):
        return web.Response(status=403, text="bad signature")
    try:
        payload = json.loads(raw)
    except Exception:
        return web.Response(status=400, text="bad json")
    _bg(handle_payload(payload))      # Meta'ga darhol 200 qaytaramiz
    return web.Response(text="ok")


def register(app: web.Application):
    app.router.add_get(PATH, _verify)
    app.router.add_post(PATH, _receive)
    log.info("igflow webhook: %s (token: %d ta, imzo kaliti: %s, verify: %s)", PATH, len(_tokens()),
             bool(APP_SECRET), bool(VERIFY_TOKEN))


def setup(bot):
    """Xatolar haqida adminlarga xabar berish uchun botni eslab qoladi."""
    global _bot
    _bot = bot
    try:
        asyncio.get_running_loop().create_task(_refresher())
    except RuntimeError:
        pass


# ---------- Telegram buyruqlari (faqat adminlar) ----------

router = Router()
router.message.filter(F.from_user.id.in_(ADMIN_IDS), F.chat.type == ChatType.PRIVATE)

HELP = (
    "📩 <b>Instagram avtomatlashtirish</b>\n\n"
    "<b>Qoida qo'shish:</b>\n"
    "<code>/qoida_qosh kalit1, kalit2 | izohga javob | DM matni</code>\n"
    "Izohga javob yoki DM kerak bo'lmasa, o'rniga <code>-</code> yozing.\n"
    "Misol:\n<code>/qoida_qosh narx, price | Direct'ga yubordik 📩 | Salom! Narxlar: ...</code>\n\n"
    "📌 <b>Faqat bitta postga:</b> oxiriga post havolasini qo'shing:\n"
    "<code>/qoida_qosh narx | Direct'ga yubordik 📩 | Narxlar: ... | https://www.instagram.com/p/ABC123/</code>\n"
    "(Bunday qoida faqat o'sha postdagi izohlarga ishlaydi va umumiy qoidadan ustun turadi.)\n\n"
    "/qoida — qoidalar ro'yxati\n"
    "/qoida_ochir 2 — 2-qoidani o'chirish\n"
    "/dm_avto Matn — kalit so'z topilmagan DM'larga umumiy javob (o'chirish: <code>/dm_avto off</code>)\n"
    "/izoh_avto Matn — <b>har bir</b> izohga (kalit so'zsiz ham) ochiq javob. Bir nechta variant: "
    "<code>/izoh_avto Rahmat! 🙏 || Fikringiz uchun rahmat 😊 || Qo'llab-quvvatlaganingiz uchun rahmat ❤️</code> "
    "(o'chirish: <code>/izoh_avto off</code>)\n"
    "/token_yangila — Instagram tokenini hozir yangilash (o'zi ham har 7 kunda yangilanadi)\n"
    "/igflow — holatni tekshirish"
)


@router.message(Command("qoida"))
async def cmd_rules(m: Message):
    rules = _rules()
    if not rules:
        dr = _default_reply()
        await m.answer("Qoidalar yo'q.\n"
                       f"💬 Har bir izohga javob: {html.escape(dr[:150]) if dr else 'o‘chiq'}\n\n" + HELP)
        return
    lines = []
    for r in rules:
        pin = f"   📌 {html.escape(r.get('post') or 'bitta post')}\n" if r.get("media") else ""
        lines.append(f"<b>{r['id']}.</b> 🔑 {html.escape(', '.join(r['kw']))}\n{pin}"
                     f"   💬 {html.escape(r.get('reply') or '—')}\n"
                     f"   📩 {html.escape((r.get('dm') or '—')[:150])}")
    dm = _default_dm()
    dr = _default_reply()
    lines.append(f"\n🤖 Umumiy DM javobi: {html.escape(dm[:150]) if dm else 'o‘chiq'}\n"
                 f"💬 Har bir izohga javob: {html.escape(dr[:150]) if dr else 'o‘chiq'}")
    await m.answer("\n\n".join(lines))


@router.message(Command("qoida_qosh"))
async def cmd_add(m: Message, command: CommandObject):
    parts = [p.strip() for p in (command.args or "").split("|")]
    if len(parts) not in (3, 4) or not parts[0]:
        await m.answer("Format: <code>/qoida_qosh kalit1, kalit2 | izohga javob | DM matni</code>\n"
                       "Faqat bitta postga: oxiriga <code>| post havolasi</code> qo'shing.\n"
                       "Kerak bo'lmagan joyga <code>-</code> yozing.")
        return
    kws = [k.strip().lower() for k in parts[0].split(",") if k.strip()]
    reply = "" if parts[1] in ("-", "") else parts[1]
    dm = "" if parts[2] in ("-", "") else parts[2]
    if not kws or not (reply or dm):
        await m.answer("Kamida bitta kalit so'z va izohga javob yoki DM matni kerak.")
        return
    rule = {"kw": kws, "reply": reply, "dm": dm}
    if len(parts) == 4 and parts[3] not in ("", "-"):
        try:
            mid, link = await resolve_post(parts[3])
        except IgError as e:
            await m.answer(f"❌ {html.escape(str(e))}")
            return
        rule["media"] = [mid]
        rule["post"] = link
    rules = _rules()
    nid = max([r["id"] for r in rules], default=0) + 1
    rule["id"] = nid
    rules.append(rule)
    _save_rules(rules)
    extra = f"\n📌 Faqat shu postda: {html.escape(rule['post'])}" if rule.get("post") else ""
    await m.answer(f"✅ {nid}-qoida qo'shildi: 🔑 {html.escape(', '.join(kws))}{extra}")


@router.message(Command("token_yangila"))
async def cmd_refresh(m: Message):
    done, errs = await refresh_tokens(force=True)
    txt = f"🔄 Yangilandi: {done} ta token (yana 60 kun)."
    if errs:
        txt += "\n❌ " + html.escape("\n".join(errs[:3]))
    await m.answer(txt)


@router.message(Command("qoida_ochir"))
async def cmd_del(m: Message, command: CommandObject):
    arg = (command.args or "").strip()
    rules = _rules()
    new = [r for r in rules if str(r["id"]) != arg]
    if not arg or len(new) == len(rules):
        await m.answer("Raqamni ko'rsating: <code>/qoida_ochir 2</code> (ro'yxat: /qoida)")
        return
    _save_rules(new)
    await m.answer(f"🗑 {arg}-qoida o'chirildi.")


@router.message(Command("dm_avto"))
async def cmd_default_dm(m: Message, command: CommandObject):
    arg = (command.args or "").strip()
    if not arg:
        dm = _default_dm()
        await m.answer(f"Umumiy DM javobi: {html.escape(dm) if dm else 'o‘chiq'}\n"
                       "O'rnatish: <code>/dm_avto Matn</code>, o'chirish: <code>/dm_avto off</code>")
        return
    if arg.lower() == "off":
        db.kv_set(KV_DEFAULT_DM, "")
        await m.answer("Umumiy DM javobi o'chirildi.")
        return
    db.kv_set(KV_DEFAULT_DM, arg)
    await m.answer("✅ Umumiy DM javobi o'rnatildi (bir odamga 12 soatda bir marta yuboriladi).")


@router.message(Command("izoh_avto"))
async def cmd_default_reply(m: Message, command: CommandObject):
    arg = (command.args or "").strip()
    if not arg:
        dr = _default_reply()
        await m.answer(f"Har bir izohga javob: {html.escape(dr) if dr else 'o‘chiq'}\n"
                       "O'rnatish: <code>/izoh_avto Matn</code> (bir nechta variant: <code>A || B || C</code>), "
                       "o'chirish: <code>/izoh_avto off</code>")
        return
    if arg.lower() == "off":
        db.kv_set(KV_DEFAULT_REPLY, "")
        await m.answer("Har bir izohga javob o'chirildi.")
        return
    if not _pick(arg):
        await m.answer("Javob matni bo'sh. Misol: <code>/izoh_avto Rahmat! 🙏</code>")
        return
    db.kv_set(KV_DEFAULT_REPLY, arg)
    n = len([v for v in arg.split("||") if v.strip()])
    mins = COMMENT_COOLDOWN // 60
    await m.answer(f"✅ Har bir izohga ochiq javob yoqildi ({n} ta variant). Bir odamga {mins} daqiqada bir marta "
                   "javob beriladi. Kalit so'zli qoidalar avvalgidek ishlaydi.")


@router.message(Command("igflow"))
async def cmd_status(m: Message):
    domain = os.getenv("RAILWAY_PUBLIC_DOMAIN", "").strip()
    url = f"https://{domain}{PATH}" if domain else f"https://&lt;domen&gt;{PATH}"
    lines = [f"🌐 Webhook: <code>{url}</code>",
             f"🔐 IGFLOW_APP_SECRET: {'✅' if APP_SECRET else '❌'}   IGFLOW_VERIFY_TOKEN: {'✅' if VERIFY_TOKEN else '❌'}",
             f"📋 Qoidalar: {len(_rules())} ta   💬 Har bir izohga javob: {'✅' if _default_reply() else 'o‘chiq'}"]
    toks = _tokens()
    if not toks:
        lines.append("❌ IGFLOW_TOKEN o'rnatilmagan")
    async with aiohttp.ClientSession(timeout=_TIMEOUT) as s:
        for i, t in enumerate(toks, 1):
            try:
                uid, name = await _whoami(s, t)
                _acc_token[uid], _acc_name[uid] = t, name
                lines.append(f"✅ Akkaunt {i}: @{html.escape(name)}")
            except Exception as e:
                lines.append(f"❌ Akkaunt {i}: {html.escape(str(e))[:150]}")
    await m.answer("\n".join(lines))
