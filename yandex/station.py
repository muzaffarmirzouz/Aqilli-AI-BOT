"""Yandex Stansiyaga (Alisa) bulut orqali gapirtirish.

Rasmiy ochiq API yo'q, shuning uchun Yandex "Umnyy dom" ilovasining o'zi ishlatadigan
iot.quasar.yandex.ru manzillari ishlatiladi (Home Assistant'dagi YandexStation integratsiyasi
ham shu usulda ishlaydi):
  1) akkauntda bitta yordamchi stsenariy yaratiladi ("Namoz eslatma bot");
  2) gapirish kerak bo'lganda stsenariydagi matn yangilanadi va stsenariy ishga tushiriladi —
     kolonka matnni ovoz chiqarib aytadi (quasar tts).
Kirish: brauzerdan olingan yandex.ru cookie'lari (Session_id va boshqalar).
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
def cookie() -> str:
    return db.kv_get("ys_cookie") or ""


def set_cookie(raw: str):
    db.kv_set("ys_cookie", clean_cookie(raw))
    db.kv_set("ys_scenario", "")  # yangi akkaunt bo'lishi mumkin


def device_id() -> str:
    return db.kv_get("ys_device") or ""


def _yandex_domain(d: str) -> bool:
    d = d.lstrip(".").lower()
    return d == "yandex.ru" or d.endswith(".yandex.ru")


def clean_cookie(raw: str) -> str:
    """Cookie'ni 'nom=qiymat; ...' ko'rinishiga keltiradi. Qabul qiladi:
    - brauzer Headers'idan nusxa olingan 'Cookie: ...' qatori;
    - "Get cookies.txt LOCALLY" kabi kengaytmalarning Netscape (cookies.txt) fayli;
    - JSON eksport ([{"name","value","domain"}, ...])."""
    s = raw.strip().lstrip("﻿")
    pairs: dict[str, str] = {}
    if s.startswith("[") or s.startswith("{"):
        try:
            data = json.loads(s)
            items = data if isinstance(data, list) else data.get("cookies", [])
            for c in items:
                if c.get("name") and _yandex_domain(str(c.get("domain", "yandex.ru"))):
                    pairs[c["name"]] = str(c.get("value", ""))
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
            if len(f) >= 7 and _yandex_domain(f[0]):
                pairs[f[5]] = f[6]
    if pairs:
        return "; ".join(f"{k}={v}" for k, v in pairs.items())
    s = re.sub(r"^(cookie|Cookie)\s*:\s*", "", s)
    return " ".join(s.split())


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
    def __init__(self, raw_cookie: str | None = None):
        self.cookie = clean_cookie(raw_cookie) if raw_cookie else cookie()
        self.csrf = ""

    async def __aenter__(self):
        if not self.cookie:
            raise YandexError("Yandex akkaunti ulanmagan")
        self.s = aiohttp.ClientSession(
            headers={"Cookie": self.cookie, "User-Agent": UA},
            timeout=aiohttp.ClientTimeout(total=20),
        )
        return self

    async def __aexit__(self, *a):
        await self.s.close()

    async def _csrf(self) -> str:
        m = None
        for url in (CSRF_PAGE, CSRF_PAGE + "?storage=1", "https://yandex.ru/quasar/iot"):
            try:
                async with self.s.get(url) as r:
                    html = await r.text()
            except Exception as e:
                log.warning("csrf %s: %s", url, e)
                continue
            m = re.search(r'"csrfToken2"\s*:\s*"([^"]+)"', html) or re.search(r'"csrfToken"\s*:\s*"([^"]+)"', html)
            if m:
                break
        if not m:
            raise YandexError("Yandex'ga kirib bo'lmadi — cookie eskirgan yoki noto'g'ri. "
                              "Qaytadan ulang.")
        self.csrf = m.group(1)
        return self.csrf

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
                        raise YandexError("Yandex akkauntiga kirish rad etildi — cookie eskirgan. Qaytadan ulang.")
                    raise YandexError(f"Yandex javobi {r.status}: {text[:200]}")
                return data
        raise YandexError("Yandex ruxsat bermadi (403). Cookie'ni yangilang.")

    # ---------- qurilmalar ----------
    async def speakers(self) -> list[dict]:
        """Akkauntdagi barcha aqlli kolonkalar: [{'id','name'}]."""
        data = await self.req("GET", f"{IOT_V3}/devices")
        found: dict[str, str] = {}

        def walk(o):
            if isinstance(o, dict):
                t = str(o.get("type", ""))
                if o.get("id") and o.get("name") and t.startswith("devices.types.smart_speaker"):
                    found[o["id"]] = o["name"]
                for v in o.values():
                    walk(v)
            elif isinstance(o, list):
                for v in o:
                    walk(v)

        walk(data)
        if not found:  # eski format
            walk(await self.req("GET", f"{IOT}/devices"))
        return [{"id": k, "name": v} for k, v in found.items()]

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
        data = await self.req("GET", f"{IOT}/scenarios")
        for sc in data.get("scenarios", []):
            if sc.get("name") in (SCENARIO_NAME, OLD_SCENARIO_NAME):
                return sc["id"]
        return ""

    async def _scenario(self, dev: str, text: str) -> tuple[str, bool]:
        """(stsenariy id, hozir yaratildimi)."""
        sid = db.kv_get("ys_scenario") or await self._find_scenario()
        created = False
        if not sid:
            try:
                data = await self.req("POST", f"{IOT_V4}/scenarios", self._payload(dev, text))
            except YandexError as e:
                raise YandexError(f"Stsenariy yaratishda xato: {e}")
            sid = data.get("scenario_id") or await self._find_scenario()
            if not sid:
                raise YandexError("Yandex'da stsenariy yaratib bo'lmadi")
            created = True
        db.kv_set("ys_scenario", sid)
        return sid, created

    async def say(self, text: str, dev: str | None = None):
        dev = dev or device_id()
        if not dev:
            raise YandexError("Kolonka tanlanmagan")
        text = text.strip()[:MAX_PHRASE]
        sid, created = await self._scenario(dev, text)
        if not created:
            try:
                await self.req("PUT", f"{IOT_V4}/scenarios/{sid}", self._payload(dev, text))
            except YandexError as e:
                # stsenariy ilovada o'chirilgan bo'lsagina qaytadan yaratamiz (dublikat bo'lmasin)
                if await self._find_scenario() == sid:
                    raise YandexError(f"Matnni yangilashda xato: {e}")
                db.kv_set("ys_scenario", "")
                sid, _ = await self._scenario(dev, text)
        try:
            await self.req("POST", f"{IOT}/scenarios/{sid}/actions")
        except YandexError as e:
            raise YandexError(f"Stsenariyni ishga tushirishda xato: {e}")


async def say(text: str, cyrillic: bool = True):
    async with Client() as c:
        await c.say(to_cyrillic(text) if cyrillic else text)
