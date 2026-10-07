"""Facebook sahifa va Instagram (Business/Creator) akkauntga post joylash — Meta Graph API.

Sozlash (Railway o'zgaruvchilari):
  META_PAGE_ID     — Facebook sahifa ID raqami
  META_PAGE_TOKEN  — sahifaning uzoq muddatli Page Access Token'i
  META_IG_USER_ID  — (ixtiyoriy) Instagram Business akkaunt ID; bo'sh bo'lsa sahifadan avtomatik topiladi
  META_API_VERSION — (ixtiyoriy) standart v21.0
"""
import asyncio
import logging
import os

import aiohttp

log = logging.getLogger(__name__)

PAGE_ID = os.getenv("META_PAGE_ID", "").strip()
TOKEN = os.getenv("META_PAGE_TOKEN", "").strip()
IG_ID = os.getenv("META_IG_USER_ID", "").strip()
VER = os.getenv("META_API_VERSION", "v21.0").strip()
BASE = f"https://graph.facebook.com/{VER}"
_TIMEOUT = aiohttp.ClientTimeout(total=90)
IG_CAPTION_LIMIT = 2200


class MetaError(Exception):
    pass


def fb_enabled() -> bool:
    return bool(PAGE_ID and TOKEN)


def ig_enabled() -> bool:
    return fb_enabled()  # IG ham sahifa tokeni orqali ishlaydi; ID avtomatik topiladi


async def _call(session, method: str, path: str, **kw):
    kw.setdefault("params", {})["access_token"] = TOKEN
    async with session.request(method, f"{BASE}/{path}", **kw) as r:
        data = await r.json(content_type=None)
    if isinstance(data, dict) and data.get("error"):
        err = data["error"]
        raise MetaError(f"{err.get('message')} (kod {err.get('code')})")
    return data


async def _ig_user(session) -> str:
    global IG_ID
    if IG_ID:
        return IG_ID
    data = await _call(session, "GET", PAGE_ID, params={"fields": "instagram_business_account"})
    iba = (data.get("instagram_business_account") or {}).get("id")
    if not iba:
        raise MetaError("Facebook sahifaga Instagram Business/Creator akkaunt ulanmagan")
    IG_ID = iba
    return IG_ID


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
        ig = await _ig_user(s)
        # Instagram rasmni ochiq URL'dan oladi: rasmni Facebook'ga e'lon qilinmagan holda yuklab, manzilini olamiz
        hidden = await _upload_photo(s, image, published=False)
        url = await _photo_url(s, hidden["id"])
        cont = await _call(s, "POST", f"{ig}/media",
                           data={"image_url": url, "caption": caption[:IG_CAPTION_LIMIT]})
        cid = cont["id"]
        for _ in range(20):  # Instagram rasmni qayta ishlaguncha kutamiz
            st = await _call(s, "GET", cid, params={"fields": "status_code"})
            if st.get("status_code") == "FINISHED":
                break
            if st.get("status_code") == "ERROR":
                raise MetaError("Instagram rasmni qabul qilmadi")
            await asyncio.sleep(3)
        pub = await _call(s, "POST", f"{ig}/media_publish", data={"creation_id": cid})
        try:
            info = await _call(s, "GET", pub["id"], params={"fields": "permalink"})
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
            ig = await _ig_user(s)
            info = await _call(s, "GET", ig, params={"fields": "username"})
            out += f"\n✅ Instagram: @{info.get('username')}"
        except MetaError as e:
            out += f"\n❌ Instagram: {e}"
        return out
