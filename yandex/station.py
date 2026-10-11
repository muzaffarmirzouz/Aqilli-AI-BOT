"""Yandex Stansiyaga (Alisa) bulut orqali gapirtirish.

Rasmiy ochiq API yo'q, shuning uchun Yandex "Umnyy dom" ilovasining o'zi ishlatadigan
iot.quasar.yandex.ru manzillari ishlatiladi (Home Assistant'dagi YandexStation integratsiyasi
ham shu usulda ishlaydi):
  1) akkauntda bitta yordamchi stsenariy yaratiladi ("Namoz eslatma bot");
  2) gapirish kerak bo'lganda stsenariydagi matn yangilanadi va stsenariy ishga tushiriladi —
     kolonka matnni ovoz chiqarib aytadi (quasar tts).
Kirish: brauzer cookie'lari bir marta Yandex x-token'iga almashtiriladi (bazada faqat token saqlanadi),
har ulanishda token bilan yangi sessiya ochiladi.
"""
import json
import logging
import re

import aiohttp

from bot import db

log = logging.getLogger(__name__)

IOT = "https://iot.quasar.yandex.ru/m/user"
IOT_V3 = "https://iot.quasar.yandex.ru/m/v3/user"
IOT_V4 = "https://iot.quasar.yandex.ru/m/v4/user"
CSRF_PAGE = "https://yandex.ru/quasar"
SCENARIO_NAME = "Namoz eslatma bot"
OLD_SCENARIO_NAME = "Namoz eslatma (bot)"
SCENARIO_TRIGGER = "напоминание о намазе от бота"
MAX_PHRASE = 100  # Yandex bitta iborani 100 belgigacha qabul qiladi
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


class YandexError(Exception):
    pass


# ---------------- sozlamalar (bazada) ----------------
def token() -> str:
    """Yandex x-token (cookie'dan bir marta olinadi, ~1 yil amal qiladi)."""
    return db.kv_get("ys_xtoken") or ""


def set_token(x_token: str, login: str = ""):
    db.kv_set("ys_xtoken", x_token)
    db.kv_set("ys_login", login)
    db.kv_set("ys_cookie", "")     # eski versiyadan qolgan cookie kerak emas
    db.kv_set("ys_scenario", "")   # yangi akkaunt bo'lishi mumkin


def login_name() -> str:
    return db.kv_get("ys_login") or ""


def device_id() -> str:
    return db.kv_get("ys_device") or ""


def _domain(d: str) -> str:
    """'.passport.yandex.uz' -> 'yandex.uz' (Yandex domeni bo'lmasa — '')."""
    m = re.search(r"(yandex\.[a-z]{2,3})$", d.lstrip(".").lower())
    return m.group(1) if m else ""


def parse_cookies(raw: str) -> tuple[str, str]:
    """(cookie qatori, passport host). Qabul qiladi:
    - brauzer Headers'idan nusxa olingan 'Cookie: ...' qatori;
    - "Get cookies.txt LOCALLY" kabi kengaytmalarning Netscape (cookies.txt) fayli;
    - JSON eksport ([{"name","value","domain"}, ...])."""
    s = raw.strip().lstrip("\ufeff")
    by_dom: dict[str, dict[str, str]] = {}
    if s.startswith("[") or s.startswith("{"):
        try:
            data = json.loads(s)
            items = data if isinstance(data, list) else data.get("cookies", [])
            for c in items:
                dom = _domain(str(c.get("domain", "")))
                if c.get("name") and dom:
                    by_dom.setdefault(dom, {})[c["name"]] = str(c.get("value", ""))
        except ValueError:
            pass
    elif "\t" in s:
        for line in s.splitlines():
            line = line.strip()
            if line.startswith("#HttpOnly_"):
                line = line[len("#HttpOnly_"):]
            elif not line or line.startswith("#"):
                continue
            f = line.split("\t")
            if len(f) >= 7 and _domain(f[0]):
                by_dom.setdefault(_domain(f[0]), {})[f[5]] = f[6]
    if by_dom:
        # Session_id bor domenni tanlaymiz, yandex.ru ustun
        doms = sorted(by_dom, key=lambda d: ("Session_id" not in by_dom[d], d != "yandex.ru"))
        dom = doms[0]
        return "; ".join(f"{k}={v}" for k, v in by_dom[dom].items()), f"passport.{dom}"
    s = re.sub(r"^(cookie|Cookie)\s*:\s*", "", s)
    return " ".join(s.split()), "passport.yandex.ru"


# Yandex mobil ilovasining ochiq client_id/secret'i (Home Assistant YandexStation ham shuni ishlatadi)
_CLIENT_ID = "c0ebe342af7d48fbbbfcf2d2eedb8f9e"
_CLIENT_SECRET = "ad0a908f0aa341a182a37ecd75bc319e"
_MP = "https://mobileproxy.passport.yandex.net/1/bundle"


async def cookies_to_token(raw: str) -> tuple[str, str]:
    """Brauzer cookie'laridan x-token oladi. Natija: (x_token, login)."""
    cookies, host = parse_cookies(raw)
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as s:
        async with s.post(f"{_MP}/oauth/token_by_sessionid",
                          data={"client_id": _CLIENT_ID, "client_secret": _CLIENT_SECRET},
                          headers={"Ya-Client-Host": host, "Ya-Client-Cookie": cookies}) as r:
            text = await r.text()
        try:
            x_token = json.loads(text).get("access_token")
        except ValueError:
            x_token = None
        if not x_token:
            raise YandexError("Cookie'dan Yandex'ga kirib bo'lmadi — cookie eskirgan yoki to'liq emas. "
                              f"Yandex'dan qaytadan chiqib-kirib, yangi cookie yuboring. ({text[:150]})")
        login = ""
        try:
            async with s.get(f"{_MP}/account/short_info/?avatar_size=islands-300",
                             headers={"Authorization": f"OAuth {x_token}"}) as r:
                info = json.loads(await r.text())
                login = info.get("display_login") or info.get("display_name") or ""
        except Exception:
            pass
        return x_token, login


# ---------------- Lotin -> kirill (ruscha talaffuz uchun) ----------------
_LAT = [
    ("yo'", "йо"), ("o'", "о"), ("g'", "г"),
    ("sh", "ш"), ("ch", "ч"), ("yo", "ё"), ("yu", "ю"), ("ya", "я"), ("ye", "е"),
    ("a", "а"), ("b", "б"), ("d", "д"), ("e", "е"), ("f", "ф"), ("g", "г"), ("h", "х"),
    ("i", "и"), ("j", "ж"), ("k", "к"), ("l", "л"), ("m", "м"), ("n", "н"), ("o", "о"),
    ("p", "п"), ("q", "к"), ("r", "р"), ("s", "с"), ("t", "т"), ("u", "у"), ("v", "в"),
    ("x", "х"), ("y", "й"), ("z", "з"), ("'", ""),
]


def to_cyrillic(text: str) -> str:
    """O'zbek lotinidagi matnni rus ovozi to'g'ri o'qiydigan kirillga o'giradi.
    Matn allaqachon kirillda bo'lsa — o'zgarmaydi."""
    if not re.search(r"[A-Za-z]", text):
        return text
    text = re.sub(r"[‘’ʻʼ`]", "'", text)
    out, i, low = [], 0, text.lower()
    while i < len(text):
        for lat, cyr in _LAT:
            if low.startswith(lat, i):
                # so'z boshidagi "e" -> "э" (ruscha "э" kabi o'qilsin)
                if lat == "e" and (i == 0 or not low[i - 1].isalpha()):
                    cyr = "э"
                if text[i].isupper() and cyr:
                    cyr = cyr[0].upper() + cyr[1:]
                out.append(cyr)
                i += len(lat)
                break
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


# ---------------- HTTP ----------------
class Client:
    def __init__(self, x_token: str | None = None):
        self.x_token = x_token or token()
        self.csrf = ""

    async def __aenter__(self):
        if not self.x_token:
            raise YandexError("Yandex akkaunti ulanmagan")
        self.s = aiohttp.ClientSession(
            headers={"User-Agent": UA},
            cookie_jar=aiohttp.CookieJar(),
            timeout=aiohttp.ClientTimeout(total=20),
        )
        try:
            await self._login()
        except BaseException:
            await self.s.close()
            raise
        return self

    async def __aexit__(self, *a):
        await self.s.close()

    async def _login(self):
        """x-token bilan yangi sessiya cookie'larini oladi (Home Assistant usuli)."""
        async with self.s.post(f"{_MP}/auth/x_token/",
                               data={"type": "x-token", "retpath": "https://www.yandex.ru"},
                               headers={"Ya-Consumer-Authorization": f"OAuth {self.x_token}"}) as r:
            text = await r.text()
        try:
            resp = json.loads(text)
        except ValueError:
            resp = {}
        if resp.get("status") != "ok":
            raise YandexError("Yandex token eskirgan yoki bekor qilingan — akkauntni qaytadan ulang. "
                              f"({text[:150]})")
        host = resp.get("passport_host") or "https://passport.yandex.ru"
        async with self.s.get(f"{host}/auth/session/", params={"track_id": resp["track_id"]},
                              allow_redirects=False) as r:
            loc = r.headers.get("Location", "")
        if "/auth/finish" not in loc:
            raise YandexError(f"Yandex sessiyasini ochib bo'lmadi (javob: {r.status} {loc[:100]})")

    async def _csrf(self) -> str:
        """Yozish (POST/PUT) uchun x-csrf-token. Bir nechta manbadan urinadi."""
        tried = []
        for url in (CSRF_PAGE, CSRF_PAGE + "?storage=1", "https://yandex.ru/iot",
                    "https://quasar.yandex.ru/csrf_token", "https://frontend.vh.yandex.ru/csrf_token"):
            try:
                async with self.s.get(url, headers={"Accept": "text/html,application/json,*/*"}) as r:
                    body = await r.text()
                    status, final = r.status, str(r.url)
            except Exception as e:
                tried.append(f"{url.split('//')[1]} → {type(e).__name__}")
                continue
            tok = None
            m = re.search(r'"csrfToken2"\s*:\s*"([^"]+)"', body) or re.search(r'"csrfToken"\s*:\s*"([^"]+)"', body)
            if m:
                tok = m.group(1)
            elif url.endswith("/csrf_token") and status == 200:
                try:
                    j = json.loads(body)
                    tok = j.get("token") or j.get("csrf_token") if isinstance(j, dict) else None
                except ValueError:
                    t = body.strip()
                    if 10 < len(t) < 200 and " " not in t and "<" not in t:
                        tok = t
            if tok:
                log.info("csrf: %s", url)
                self.csrf = tok
                return tok
            note = "captcha" if "captcha" in (final + body[:3000]).lower() else f"{status}"
            if final != url:
                note += f", → {final.split('//')[-1][:60]}"
            tried.append(f"{url.split('//')[1]} → {note}")
        log.warning("csrf olinmadi: %s", tried)
        raise YandexError("Yandex CSRF tokenini olib bo'lmadi. Tekshirilgan manzillar: " + "; ".join(tried))

    async def req(self, method: str, url: str, payload: dict | None = None) -> dict:
        for attempt in range(2):
            headers = {}
            if method != "GET":
                headers["x-csrf-token"] = self.csrf or await self._csrf()
            async with self.s.request(method, url, json=payload, headers=headers) as r:
                text = await r.text()
                if r.status in (401, 403) and attempt == 0:
                    await self._csrf()  # token eskirgan bo'lishi mumkin
                    continue
                try:
                    data = json.loads(text) if text else {}
                except ValueError:
                    data = {}
                if r.status >= 400 or (data and data.get("status") not in (None, "ok")):
                    if r.status == 401:
                        raise YandexError("Yandex akkauntiga kirish rad etildi — akkauntni qaytadan ulang.")
                    raise YandexError(f"Yandex javobi {r.status}: {text[:200]}")
                return data
        raise YandexError("Yandex ruxsat bermadi (403). Akkauntni qaytadan ulang.")

    # ---------- qurilmalar ----------
    async def speakers(self) -> list[dict]:
        """Akkauntdagi Alisa'li kolonkalar: [{'id','name'}] (v3 ro'yxatidagi quasar_info bo'yicha)."""
        try:
            data = await self.req("GET", f"{IOT_V3}/devices")
        except YandexError as e:
            raise YandexError(f"Kolonkalar ro'yxatini olishda xato: {e}")
        out, seen = [], set()
        for house in data.get("households", []) or []:
            for d in house.get("all", []) or []:
                info = d.get("quasar_info") or {}
                is_speaker = (bool(info) and info.get("platform") not in {"saturn", "mike", "cherry"}) \
                    or str(d.get("type", "")).startswith("devices.types.smart_speaker")
                if is_speaker and d.get("id") and d["id"] not in seen and not d.get("sharing_info"):
                    seen.add(d["id"])
                    name = d.get("name") or "Kolonka"
                    if len(data.get("households", [])) > 1 and house.get("name"):
                        name = f"{name} ({house['name']})"
                    out.append({"id": d["id"], "name": name})
        return out

    # ---------- stsenariy ----------
    @staticmethod
    def _payload(dev: str, text: str) -> dict:
        """Ovozli trigger + kolonkada TTS (Yandex'ning hozirgi "actions.v2" formati)."""
        return {
            "name": SCENARIO_NAME,
            "icon": "home",
            "triggers": [{"trigger": {"type": "scenario.trigger.voice", "value": SCENARIO_TRIGGER}}],
            "steps": [{
                "type": "scenarios.steps.actions.v2",
                "parameters": {
                    "items": [{
                        "id": dev,
                        "type": "step.action.item.device",
                        "value": {
                            "id": dev,
                            "item_type": "device",
                            "capabilities": [{
                                "type": "devices.capabilities.quasar",
                                "state": {"instance": "tts", "value": {"text": text[:MAX_PHRASE]}},
                            }],
                        },
                    }],
                },
            }],
        }

    async def _find_scenario(self) -> str:
        """Avval yaratilgan stsenariyni nomidan qidiradi. Ro'yxat olinmasa (ba'zi akkauntlarda
        Yandex 500 qaytaradi) — bo'sh qaytaradi, xato bermaydi."""
        for url in (f"{IOT}/scenarios", f"{IOT_V3}/scenarios"):
            try:
                data = await self.req("GET", url)
            except YandexError as e:
                log.warning("stsenariylar ro'yxati %s: %s", url, e)
                continue
            for sc in data.get("scenarios", []) or []:
                if sc.get("name") in (SCENARIO_NAME, OLD_SCENARIO_NAME):
                    return sc["id"]
            return ""
        return ""

    async def _create(self, dev: str, text: str) -> str:
        try:
            data = await self.req("POST", f"{IOT_V4}/scenarios", self._payload(dev, text))
        except YandexError as e:
            if "ALREADY_EXISTS" in str(e).upper() or "NAME" in str(e).upper():
                sid = await self._find_scenario()
                if sid:
                    return sid
            raise YandexError(f"Stsenariy yaratishda xato: {e}")
        sid = data.get("scenario_id") or data.get("id") or ""
        if not sid:
            raise YandexError(f"Stsenariy yaratildi, lekin Yandex uning raqamini qaytarmadi: {str(data)[:150]}")
        return sid

    async def say(self, text: str, dev: str | None = None):
        dev = dev or device_id()
        if not dev:
            raise YandexError("Kolonka tanlanmagan")
        text = text.strip()[:MAX_PHRASE]
        sid = db.kv_get("ys_scenario") or ""
        if sid:
            try:
                await self.req("PUT", f"{IOT_V4}/scenarios/{sid}", self._payload(dev, text))
            except YandexError as e:
                log.warning("stsenariy %s yangilanmadi (%s) — yangisi yaratiladi", sid, e)
                sid = ""
        if not sid:
            sid = await self._find_scenario()
            if sid:
                try:
                    await self.req("PUT", f"{IOT_V4}/scenarios/{sid}", self._payload(dev, text))
                except YandexError as e:
                    raise YandexError(f"Matnni yangilashda xato: {e}")
            else:
                sid = await self._create(dev, text)
            db.kv_set("ys_scenario", sid)
        try:
            await self.req("POST", f"{IOT}/scenarios/{sid}/actions")
        except YandexError as e:
            raise YandexError(f"Stsenariyni ishga tushirishda xato: {e}")


async def say(text: str, cyrillic: bool = True):
    async with Client() as c:
        await c.say(to_cyrillic(text) if cyrillic else text)
