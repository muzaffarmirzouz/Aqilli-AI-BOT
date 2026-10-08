"""Facebook sahifa va Instagram (Business/Creator) akkauntga post joylash — Meta Graph API.

Sozlash (Railway o'zgaruvchilari):
  META_PAGE_ID     — Facebook sahifa ID raqami
  META_PAGE_TOKEN  — sahifaning uzoq muddatli Page Access Token'i
  META_IG_TOKEN    — (tavsiya) Instagram'ning o'z tokeni («API setup with Instagram login»).
                     Bu bo'lsa Instagram Facebook sahifaga ulanishi shart emas.
                     Token 60 kun yashaydi — bot uni o'zi avtomatik yangilab turadi.
  META_IG_PAGE_ID, META_IG_PAGE_TOKEN — (ixtiyoriy) Instagram ulangan BOSHQA sahifa (asosiy sahifaga ulab bo'lmasa)
  META_IG_USER_ID  — (ixtiyoriy) Instagram akkaunt ID; bo'sh bo'lsa avtomatik topiladi
  META_API_VERSION — (ixtiyoriy) standart v21.0
  META_APP_ID, META_APP_SECRET — ilova ID va maxfiy kaliti. Bular bo'lsa botga /token buyrug'i bilan
                     oddiy (qisqa) token yuborish kifoya: bot uni o'zi muddatsiz sahifa tokeniga aylantiradi
                     va bazada saqlaydi (Railway'ni o'zgartirish shart emas).
"""
import asyncio
import hashlib
import html
import logging
import os
import time

import aiohttp

from . import db

log = logging.getLogger(__name__)

PAGE_ID = os.getenv("META_PAGE_ID", "").strip()
TOKEN = os.getenv("META_PAGE_TOKEN", "").strip()
IG_ENV_TOKEN = os.getenv("META_IG_TOKEN", "").strip()
# Instagram boshqa Facebook sahifaga ulangan bo'lsa (masalan asosiy sahifa portfelda bo'lib, ulab bo'lmasa)
IG_PAGE_ID = os.getenv("META_IG_PAGE_ID", "").strip()
IG_PAGE_TOKEN = os.getenv("META_IG_PAGE_TOKEN", "").strip()
IG_ID = os.getenv("META_IG_USER_ID", "").strip()
APP_ID = os.getenv("META_APP_ID", "").strip()
APP_SECRET = os.getenv("META_APP_SECRET", "").strip()
VER = os.getenv("META_API_VERSION", "v21.0").strip()
BASE = f"https://graph.facebook.com/{VER}"
IG_BASE = f"https://graph.instagram.com/{VER}"
_TIMEOUT = aiohttp.ClientTimeout(total=90)
IG_CAPTION_LIMIT = 2200
_REFRESH_EVERY = 7 * 24 * 3600  # haftada bir yangilaymiz (token 60 kun yashaydi)


class MetaError(Exception):
    pass


def _sync_env():
    """Railway'ga yangi token qo'yilsa — bazadagi eskisini unutamiz (Railway ustun bo'ladi)."""
    sig = hashlib.sha256(f"{TOKEN}|{IG_PAGE_ID}|{IG_PAGE_TOKEN}".encode()).hexdigest()[:16]
    if db.kv_get("meta_env_sig") != sig:
        db.kv_set("meta_env_sig", sig)
        for k in ("meta_page_token", "meta_ig_page_id", "meta_ig_page_token"):
            db.kv_set(k, "")


def _page_token() -> str:
    """Amaldagi sahifa tokeni: /token bilan yangilangani (bazada) Railway'dagidan ustun."""
    _sync_env()
    return db.kv_get("meta_page_token") or TOKEN


def _ig_page() -> tuple[str, str]:
    _sync_env()
    pid = db.kv_get("meta_ig_page_id") or IG_PAGE_ID
    tok = db.kv_get("meta_ig_page_token") or IG_PAGE_TOKEN
    return pid, tok


def fb_enabled() -> bool:
    return bool(PAGE_ID and _page_token())


def ig_enabled() -> bool:
    return fb_enabled()


# ---------- Instagram tokeni (Instagram login) ----------

def _env_sig() -> str:
    return hashlib.sha256(IG_ENV_TOKEN.encode()).hexdigest()[:16] if IG_ENV_TOKEN else ""


def _ig_token() -> str:
    """Amaldagi Instagram tokeni: yangilangan nusxa bazada, Railway'dagi yangi token esa ustun."""
    if not IG_ENV_TOKEN:
        return ""
    if db.kv_get("ig_token_src") != _env_sig():
        # Railway'ga yangi token qo'yilgan — eskisini unutamiz
        db.kv_set("ig_token_src", _env_sig())
        db.kv_set("ig_token", IG_ENV_TOKEN)
        db.kv_set("ig_token_at", str(int(time.time())))
    return db.kv_get("ig_token") or IG_ENV_TOKEN


def ig_login_mode() -> bool:
    return bool(IG_ENV_TOKEN)


async def refresh_ig_token(force: bool = False) -> bool:
    """Instagram tokenini yangilaydi (yana 60 kun). Muvaffaqiyatli bo'lsa True."""
    tok = _ig_token()
    if not tok:
        return False
    last = int(db.kv_get("ig_token_at") or 0)
    if not force and time.time() - last < _REFRESH_EVERY:
        return False
    async with aiohttp.ClientSession(timeout=_TIMEOUT) as s:
        async with s.get("https://graph.instagram.com/refresh_access_token",
                         params={"grant_type": "ig_refresh_token", "access_token": tok}) as r:
            data = await r.json(content_type=None)
    new = (data or {}).get("access_token")
    if not new:
        log.warning("IG token yangilanmadi: %s", data)
        return False
    db.kv_set("ig_token", new)
    db.kv_set("ig_token_at", str(int(time.time())))
    log.info("IG token yangilandi")
    return True


async def ig_refresher(notify=None):
    """Fon vazifasi: Instagram login tokenini yangilaydi va sahifa tokeni ishlayotganini tekshiradi.
    Token yaroqsiz bo'lsa — adminlarga kuniga bir marta ogohlantirish."""
    fails = 0
    while True:
        try:
            if ig_login_mode():
                await refresh_ig_token()
                fails = 0
        except Exception as e:
            fails += 1
            log.warning("IG token yangilash: %s", e)
            if notify and fails == 3:
                await notify(f"⚠️ Instagram tokenini yangilab bo'lmadi: {e}\n"
                             f"Yangi token olib, Railway'dagi META_IG_TOKEN ni almashtiring.")
        try:
            if fb_enabled():
                async with aiohttp.ClientSession(timeout=_TIMEOUT) as s:
                    await _call(s, "GET", PAGE_ID, params={"fields": "name"})
        except MetaError as e:
            last = int(db.kv_get("meta_warn_at") or 0)
            if notify and time.time() - last > 20 * 3600:
                db.kv_set("meta_warn_at", str(int(time.time())))
                await notify("⚠️ <b>Facebook/Instagram tokeni ishlamayapti</b>\n"
                             f"{html.escape(str(e))}\n\nYangilash: Graph API Explorer → Generate Access Token → "
                             "tokenni nusxalab botga <code>/token TOKEN</code> deb yuboring.")
        except Exception as e:
            log.warning("meta tekshiruv: %s", e)
        await asyncio.sleep(6 * 3600)


# ---------- /token: oddiy tokenni muddatsiz sahifa tokeniga aylantirish ----------

def app_ready() -> bool:
    return bool(APP_ID and APP_SECRET)


async def _expiry(session, token: str) -> str:
    """Token qachon tugashini matn ko'rinishida qaytaradi."""
    if not app_ready():
        return ""
    data = await _call(session, "GET", "debug_token", token=f"{APP_ID}|{APP_SECRET}",
                       params={"input_token": token})
    d = data.get("data") or {}
    if not d.get("is_valid"):
        return "❌ yaroqsiz"
    exp = int(d.get("expires_at") or 0)
    if exp == 0:
        return "♾ muddatsiz"
    days = (exp - time.time()) / 86400
    return f"⏳ {days:.0f} kundan keyin tugaydi" if days >= 1 else f"⏳ {days * 24:.0f} soatdan keyin tugaydi"


async def set_from_user_token(user_token: str) -> str:
    """Graph API Explorer'dagi oddiy foydalanuvchi tokenidan muddatsiz sahifa tokenlarini olib, bazaga yozadi."""
    global IG_ID
    if not app_ready():
        raise MetaError("Railway'ga META_APP_ID va META_APP_SECRET qo'shilmagan")
    if not PAGE_ID:
        raise MetaError("META_PAGE_ID o'rnatilmagan")
    async with aiohttp.ClientSession(timeout=_TIMEOUT) as s:
        # 1) qisqa token → uzoq muddatli (60 kun) foydalanuvchi tokeni
        ex = await _call(s, "GET", "oauth/access_token", token=user_token, params={
            "grant_type": "fb_exchange_token", "client_id": APP_ID,
            "client_secret": APP_SECRET, "fb_exchange_token": user_token})
        long_user = ex.get("access_token")
        if not long_user:
            raise MetaError("Tokenni uzaytirib bo'lmadi")
        # 2) uzoq muddatli foydalanuvchi tokenidan olingan sahifa tokenlari — muddatsiz
        acc = await _call(s, "GET", "me/accounts", token=long_user, params={
            "fields": "id,name,access_token,instagram_business_account", "limit": "100"})
        pages = acc.get("data") or []
        main = next((p for p in pages if p.get("id") == PAGE_ID), None)
        if not main:
            names = ", ".join(p.get("name", "?") for p in pages) or "hech qaysi"
            raise MetaError(f"Tokenda asosiy sahifa (ID {PAGE_ID}) yo'q. Tokendagi sahifalar: {names}. "
                            "Generate Access Token oynasida sahifani belgilang")
        db.kv_set("meta_page_token", main["access_token"])
        lines = [f"✅ Facebook: {main.get('name')} — {await _expiry(s, main['access_token'])}"]
        # Instagram ulangan sahifa: avval asosiy sahifa, keyin oldin tanlangani, keyin istalgani
        with_ig = [p for p in pages if p.get("instagram_business_account")]
        old_pid = _ig_page()[0]
        ig_page = (next((p for p in with_ig if p["id"] == PAGE_ID), None)
                   or next((p for p in with_ig if p["id"] == old_pid), None)
                   or (with_ig[0] if with_ig else None))
        if ig_page:
            db.kv_set("meta_ig_page_id", ig_page["id"])
            db.kv_set("meta_ig_page_token", ig_page["access_token"])
            IG_ID = ig_page["instagram_business_account"]["id"]
            lines.append(f"✅ Instagram: {ig_page.get('name')} sahifasi orqali — "
                         f"{await _expiry(s, ig_page['access_token'])}")
        else:
            lines.append("⚠️ Instagram ulangan sahifa tokenda topilmadi (Generate oynasida Namangam va "
                         "Instagram'ni belgilang)")
        db.kv_set("meta_warn_at", "0")
        return "\n".join(lines)


# ---------- umumiy chaqiruvlar ----------

async def _call(session, method: str, path: str, base: str = BASE, token: str = "", **kw):
    kw.setdefault("params", {})["access_token"] = token or _page_token()
    async with session.request(method, f"{base}/{path}", **kw) as r:
        data = await r.json(content_type=None)
    if isinstance(data, dict) and data.get("error"):
        err = data["error"]
        raise MetaError(f"{err.get('message')} (kod {err.get('code')})")
    return data


async def _ig_user(session) -> tuple[str, str, str]:
    """(ig_id, api_base, token) — qaysi usulda ishlashni tanlaydi."""
    global IG_ID
    if ig_login_mode():
        tok = _ig_token()
        if not IG_ID:
            me = await _call(session, "GET", "me", base=IG_BASE, token=tok,
                             params={"fields": "user_id,username"})
            IG_ID = str(me.get("user_id") or me.get("id"))
        return IG_ID, IG_BASE, tok
    ig_pid, ig_ptok = _ig_page()
    pid, ptok = (ig_pid, ig_ptok or _page_token()) if ig_pid else (PAGE_ID, _page_token())
    if not IG_ID:
        data = await _call(session, "GET", pid, token=ptok, params={"fields": "instagram_business_account"})
        iba = (data.get("instagram_business_account") or {}).get("id")
        if not iba:
            raise MetaError("Facebook sahifaga Instagram ulanmagan "
                            "(boshqa sahifaga ulangan bo'lsa: META_IG_PAGE_ID va META_IG_PAGE_TOKEN)")
        IG_ID = iba
    return IG_ID, BASE, ptok


async def _upload_photo(session, image: bytes, caption: str = "", published: bool = True) -> dict:
    form = aiohttp.FormData()
    form.add_field("source", image, filename="post.jpg", content_type="image/jpeg")
    if caption:
        form.add_field("caption", caption)
    form.add_field("published", "true" if published else "false")
    return await _call(session, "POST", f"{PAGE_ID}/photos", data=form)


async def _photo_url(session, photo_id: str) -> str:
    """Facebook'ga yuklangan rasmning ochiq manzili (Instagram rasmni shu manzildan oladi)."""
    data = await _call(session, "GET", photo_id, params={"fields": "images"})
    imgs = sorted(data.get("images") or [], key=lambda i: i.get("width", 0), reverse=True)
    if not imgs:
        raise MetaError("Rasm manzili olinmadi")
    return imgs[0]["source"]


async def post_facebook(image: bytes, caption: str) -> str:
    """Sahifaga rasmli post. Post havolasini qaytaradi."""
    async with aiohttp.ClientSession(timeout=_TIMEOUT) as s:
        res = await _upload_photo(s, image, caption, published=True)
        post_id = res.get("post_id") or res.get("id")
        return f"https://www.facebook.com/{post_id}"


async def post_instagram(image: bytes, caption: str) -> str:
    """Instagram'ga rasmli post. Post havolasini qaytaradi."""
    async with aiohttp.ClientSession(timeout=_TIMEOUT) as s:
        ig, base, tok = await _ig_user(s)
        # Instagram rasmni ochiq URL'dan oladi: rasmni Facebook'ga e'lon qilinmagan holda yuklab, manzilini olamiz
        hidden = await _upload_photo(s, image, published=False)
        url = await _photo_url(s, hidden["id"])
        cont = await _call(s, "POST", f"{ig}/media", base=base, token=tok,
                           data={"image_url": url, "caption": caption[:IG_CAPTION_LIMIT]})
        cid = cont["id"]
        for _ in range(20):  # Instagram rasmni qayta ishlaguncha kutamiz
            st = await _call(s, "GET", cid, base=base, token=tok, params={"fields": "status_code"})
            if st.get("status_code") == "FINISHED":
                break
            if st.get("status_code") == "ERROR":
                raise MetaError("Instagram rasmni qabul qilmadi")
            await asyncio.sleep(3)
        pub = await _call(s, "POST", f"{ig}/media_publish", base=base, token=tok,
                          data={"creation_id": cid})
        try:
            info = await _call(s, "GET", pub["id"], base=base, token=tok, params={"fields": "permalink"})
            link = info.get("permalink", "")
        except Exception:
            link = ""
        try:  # yordamchi yashirin rasmni o'chiramiz
            await _call(s, "DELETE", hidden["id"])
        except Exception:
            pass
        return link or "https://www.instagram.com/"


async def check() -> str:
    """Sozlamalarni tekshiradi: sahifa nomi va Instagram username."""
    if not fb_enabled():
        return "❌ META_PAGE_ID va META_PAGE_TOKEN o'rnatilmagan"
    async with aiohttp.ClientSession(timeout=_TIMEOUT) as s:
        page = await _call(s, "GET", PAGE_ID, params={"fields": "name"})
        out = f"✅ Facebook sahifa: {page.get('name')}"
        try:
            exp = await _expiry(s, _page_token())
            if exp:
                out += f" ({exp})"
        except Exception:
            pass
        try:
            ig, base, tok = await _ig_user(s)
            info = await _call(s, "GET", ig if base == BASE else "me", base=base, token=tok,
                               params={"fields": "username"})
            mode = "Instagram login" if base == IG_BASE else "sahifa orqali"
            out += f"\n✅ Instagram: @{info.get('username')} ({mode})"
        except MetaError as e:
            out += f"\n❌ Instagram: {e}"
        return out
