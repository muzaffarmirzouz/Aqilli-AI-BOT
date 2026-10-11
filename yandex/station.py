"""Yandex Stansiyaga (Alisa) bulut orqali gapirtirish.

Rasmiy ochiq API yo'q, shuning uchun Yandex "Umnyy dom" ilovasining o'zi ishlatadigan
iot.quasar.yandex.ru manzillari ishlatiladi (Home Assistant'dagi YandexStation integratsiyasi
ham shu usulda ishlaydi):
  1) akkauntda bitta yordamchi stsenariy yaratiladi ("Namoz eslatma bot");
  2) gapirish kerak bo'lganda stsenariydagi matn yangilanadi va stsenariy ishga tushiriladi —
     kolonka matnni ovoz chiqarib aytadi (quasar tts).
Kirish: QR kod (Yandex ilovasida skanerlanadi) yoki brauzer cookie'lari orqali Yandex x-token olinadi.
Bazada faqat token saqlanadi (yandex/store.py, har foydalanuvchi alohida).
"""
import asyncio
import json
import random
import logging
import re

import aiohttp

from .store import Profile

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


class YandexAuthError(YandexError):
    """Token bekor qilingan/eskirgan — foydalanuvchi qaytadan ulashi kerak."""


# ---------------- cookie -> token ----------------
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
    def __init__(self, prof: Profile):
        self.p = prof
        self.x_token = prof.x_token
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
            raise YandexAuthError("Yandex akkaunti uzilgan (token bekor qilingan yoki eskirgan) — "
                                  "qaytadan ulang.")
        host = resp.get("passport_host") or "https://passport.yandex.ru"
        async with self.s.get(f"{host}/auth/session/", params={"track_id": resp["track_id"]},
                              allow_redirects=False) as r:
            loc = r.headers.get("Location", "")
        if "/auth/finish" not in loc:
            raise YandexError(f"Yandex sessiyasini ochib bo'lmadi (javob: {r.status} {loc[:100]})")

    async def _csrf(self) -> str:
        """Yozish (POST/PUT) uchun x-csrf-token. Bir nechta manbadan urinadi."""
        tried = []
        urls = [CSRF_PAGE, CSRF_PAGE + "?storage=1", "https://yandex.ru/iot",
                "https://quasar.yandex.ru/csrf_token", "https://frontend.vh.yandex.ru/csrf_token"]
        good = self.p.csrf_url
        if good in urls:  # oxirgi marta ishlagan manzil birinchi
            urls.remove(good)
            urls.insert(0, good)
        for url in urls:
            try:
                async with self.s.get(url, headers={"Accept": "text/html,application/json,*/*"},
                                      timeout=aiohttp.ClientTimeout(total=8)) as r:
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
                self.p.csrf_url = url
                self.csrf = tok
                return tok
            note = "captcha" if "captcha" in (final + body[:3000]).lower() else f"{status}"
            if final != url:
                note += f", → {final.split('//')[-1][:60]}"
            tried.append(f"{url.split('//')[1]} → {note}")
        log.warning("csrf olinmadi: %s", tried)
        raise YandexError("Yandex CSRF tokenini olib bo'lmadi. Tekshirilgan manzillar: " + "; ".join(tried))

    async def req(self, method: str, url: str, payload: dict | None = None,
                  tries: int = 4, timeout: float | None = None) -> dict:
        """JSON so'rov. 401/403 da bir marta qayta kirib/CSRF yangilab urinadi,
        5xx yoki tarmoq xatosida jami `tries` martagacha urinadi."""
        extra = {"timeout": aiohttp.ClientTimeout(total=timeout)} if timeout else {}
        auth_retry, fails, last = True, 0, ""
        while True:
            headers = {}
            if method != "GET":
                headers["x-csrf-token"] = self.csrf or await self._csrf()
            try:
                async with self.s.request(method, url, json=payload, headers=headers, **extra) as r:
                    status, text = r.status, await r.text()
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                status, text = 0, f"{type(e).__name__} {e}".strip()
            if status in (401, 403) and auth_retry:
                auth_retry = False
                if status == 401:
                    await self._login()
                await self._csrf()
                continue
            if status == 0 or status >= 500:
                fails += 1
                last = f"{status or 'tarmoq/timeout'}: {text[:200]}"
                if fails >= tries:
                    raise YandexError(f"Yandex javobi {last}")
                await asyncio.sleep(1.5 * fails)
                continue
            try:
                data = json.loads(text) if text else {}
            except ValueError:
                data = {}
            if status >= 400 or (data and data.get("status") not in (None, "ok")):
                if status == 401:
                    raise YandexAuthError("Yandex akkauntiga kirish rad etildi — akkauntni qaytadan ulang.")
                raise YandexError(f"Yandex javobi {status}: {text[:200]}")
            return data

    async def diagnose(self) -> list[str]:
        """Batafsil tekshiruv (adminga ko'rsatish uchun)."""
        out = ["✅ Token bilan Yandex'ga kirildi"]
        try:
            async with self.s.get(f"{IOT_V3}/devices") as r:
                st, body = r.status, await r.text()
            out.append(f"Qurilmalar ro'yxati: HTTP {st}")
            data = json.loads(body) if st == 200 else {}
            for h in data.get("households", []) or []:
                out.append(f"🏠 {h.get('name', '')}")
                for d in h.get("all", []) or []:
                    qi = d.get("quasar_info") or {}
                    out.append(f"  • {d.get('name')} | {str(d.get('type', '')).replace('devices.types.', '')}"
                               f" | platform={qi.get('platform', '—')}"
                               f"{' | ulashilgan' if d.get('sharing_info') else ''} | id={d.get('id', '')[:8]}…")
                    for cap in d.get("capabilities", []) or []:
                        inst = (cap.get("parameters") or {}).get("instance") or (cap.get("state") or {}).get("instance") or ""
                        out.append(f"      ↳ {str(cap.get('type', '')).replace('devices.capabilities.', '')} {inst}")
            if st != 200:
                out.append(f"  javob: {body[:200]}")
        except Exception as e:
            out.append(f"❌ Qurilmalar: {type(e).__name__}: {e}")
        for url in (f"{IOT}/scenarios", f"{IOT_V3}/scenarios"):
            try:
                async with self.s.get(url) as r:
                    st, body = r.status, await r.text()
                n = len(json.loads(body).get("scenarios", [])) if st == 200 else "—"
                out.append(f"Stsenariylar {url.split('/m/')[1]}: HTTP {st}, soni {n}"
                           + ("" if st == 200 else f" | {body[:120]}"))
            except Exception as e:
                out.append(f"❌ Stsenariylar: {type(e).__name__}: {e}")
        try:
            await self._csrf()
            out.append("✅ CSRF token olindi")
        except YandexError as e:
            out.append(f"❌ {e}")
        out.append(f"Saqlangan stsenariy: {self.p.scenario or '—'} · usul: {self.p.mode or 'aniqlanmagan'}"
                   f" · kolonka id: {self.p.device_id[:8] or '—'}…")
        self.p.mode = ""  # tekshiruvdan keyin keyingi Sinov hamma usullarni qaytadan sinaydi
        return out

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
    def _payload(dev: str, text: str, name: str = SCENARIO_NAME) -> dict:
        """Ovozli trigger + kolonkada TTS (Yandex'ning hozirgi "actions.v2" formati)."""
        return {
            "name": name,
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
                data = await self.req("GET", url, tries=1, timeout=8)
            except YandexError as e:
                log.warning("stsenariylar ro'yxati %s: %s", url, e)
                continue
            for sc in data.get("scenarios", []) or []:
                if str(sc.get("name", "")).startswith(SCENARIO_NAME) or sc.get("name") == OLD_SCENARIO_NAME:
                    return sc["id"]
            return ""
        return ""

    async def _create(self, dev: str, text: str) -> str:
        """Yangi stsenariy. Nom band bo'lsa (ro'yxat olinmaydigan akkauntlarda eski stsenariy qolgan) —
        boshqa nom bilan yaratadi."""
        name, last = SCENARIO_NAME, ""
        for _ in range(3):
            try:
                data = await self.req("POST", f"{IOT_V4}/scenarios", self._payload(dev, text, name))
            except YandexError as e:
                last = str(e)
                if "NAME_IS_ALREADY_TAKEN" in last or "ALREADY" in last.upper():
                    name = f"{SCENARIO_NAME} {random.randint(100, 999)}"
                    continue
                raise YandexError(f"Stsenariy yaratishda xato: {e}")
            sid = data.get("scenario_id") or data.get("id") or ""
            if not sid:
                raise YandexError(f"Stsenariy yaratildi, lekin Yandex uning raqamini qaytarmadi: {str(data)[:150]}")
            self.p.scenario_name = name
            return sid
        raise YandexError(f"Stsenariy yaratishda xato: {last}")

    async def _direct(self, dev: str, text: str) -> str:
        """Stsenariysiz: kolonkaga to'g'ridan-to'g'ri buyruq. Har safar sinaladi (ishlagan usul birinchi).
        Natija: "" — ishladi, aks holda xato matni."""
        variants = {
            "phrase": {"type": "devices.capabilities.quasar.server_action",
                       "state": {"instance": "phrase_action", "value": text}},
            "tts": {"type": "devices.capabilities.quasar",
                    "state": {"instance": "tts", "value": {"text": text}}},
        }
        order = list(variants)
        if self.p.mode in variants:
            order.remove(self.p.mode)
            order.insert(0, self.p.mode)
        errors = []
        for name in order:
            try:
                await self.req("POST", f"{IOT}/devices/{dev}/actions", {"actions": [variants[name]]},
                               tries=3, timeout=10)
                self.p.mode = name
                return ""
            except YandexAuthError:
                raise
            except YandexError as e:
                log.warning("to'g'ridan-to'g'ri %s: %s", name, e)
                errors.append(f"{name}: {e}")
        return "; ".join(errors)

    async def command(self, text: str, dev: str | None = None):
        """Alisa'ga ovozli buyruq bergandek (text_action): masalan «включи азан»."""
        dev = dev or self.p.device_id
        if not dev:
            raise YandexError("Kolonka tanlanmagan")
        action = {"type": "devices.capabilities.quasar.server_action",
                  "state": {"instance": "text_action", "value": text.strip()[:MAX_PHRASE]}}
        try:
            await self.req("POST", f"{IOT}/devices/{dev}/actions", {"actions": [action]}, tries=3, timeout=10)
        except YandexAuthError:
            raise
        except YandexError as e:
            raise YandexError(f"Alisa'ga buyruq yuborib bo'lmadi: {e}")

    async def say(self, text: str, dev: str | None = None):
        dev = dev or self.p.device_id
        if not dev:
            raise YandexError("Kolonka tanlanmagan")
        text = text.strip()[:MAX_PHRASE]
        direct_err = await self._direct(dev, text)
        if not direct_err:
            return
        try:
            await self._via_scenario(dev, text)
        except YandexAuthError:
            raise
        except YandexError as e:
            raise YandexError(f"Kolonkaga yuborib bo'lmadi. To'g'ridan-to'g'ri: {direct_err[:200]} | "
                              f"Stsenariy: {str(e)[:200]}")

    async def _via_scenario(self, dev: str, text: str):
        name = self.p.scenario_name or SCENARIO_NAME
        sid = self.p.scenario or ""
        if sid:
            try:
                await self.req("PUT", f"{IOT_V4}/scenarios/{sid}", self._payload(dev, text, name))
            except YandexError as e:
                log.warning("stsenariy %s yangilanmadi (%s) — yangisi yaratiladi", sid, e)
                sid = ""
        if not sid:
            sid = await self._find_scenario()
            if sid:
                try:
                    await self.req("PUT", f"{IOT_V4}/scenarios/{sid}", self._payload(dev, text, name))
                except YandexError as e:
                    raise YandexError(f"Matnni yangilashda xato: {e}")
            else:
                sid = await self._create(dev, text)
            self.p.scenario = sid
        try:
            await self.req("POST", f"{IOT}/scenarios/{sid}/actions")
        except YandexError as e:
            raise YandexError(f"Stsenariyni ishga tushirishda xato: {e}")


# Har foydalanuvchiga doimiy sessiya (har safar qaytadan kirmaslik uchun). Ko'pi bilan MAX_SESSIONS ta.
MAX_SESSIONS = 300
_sessions: dict[int, Client] = {}
_locks: dict[int, asyncio.Lock] = {}


async def _client(prof: Profile) -> Client:
    c = _sessions.get(prof.uid)
    if c and not c.s.closed and c.x_token == prof.x_token:
        c.p = prof  # eng yangi sozlamalar
        return c
    await reset(prof.uid)
    if len(_sessions) >= MAX_SESSIONS:  # eng eskisini yopamiz
        await reset(next(iter(_sessions)))
    c = Client(prof)
    await c.__aenter__()
    _sessions[prof.uid] = c
    return c


async def reset(uid: int):
    """Foydalanuvchi sessiyasini yopadi (uzganda, akkaunt almashganda yoki xatoda)."""
    c = _sessions.pop(uid, None)
    if c:
        try:
            await c.__aexit__(None, None, None)
        except Exception:
            pass


def speak_seconds(text: str) -> float:
    """Kolonka matnni taxminan necha soniyada aytib bo'ladi (keyingi buyruq uni bo'lib qo'ymasin)."""
    return 2.0 + len(text) / 11.0


async def _run(prof: Profile, fn: str, text: str):
    lock = _locks.setdefault(prof.uid, asyncio.Lock())
    async with lock:
        try:
            try:
                await getattr(await _client(prof), fn)(text)
            except YandexAuthError:
                raise
            except Exception as e:
                # sessiya eskirgan bo'lishi mumkin — yangisi bilan bir marta qayta urinamiz
                log.warning("kolonka uid=%s: %s — qayta ulanilmoqda", prof.uid, e)
                await reset(prof.uid)
                await getattr(await _client(prof), fn)(text)
        finally:
            prof.save()


async def say(prof: Profile, text: str, then_command: str | None = None):
    """Foydalanuvchining kolonkasida matnni aytadi; `then_command` berilsa, matn tugagach Alisa'ga
    shu buyruqni yuboradi (masalan «включи азан»). Profildagi o'zgarishlar saqlanadi."""
    if not prof.ready:
        raise YandexError("Akkaunt ulanmagan yoki kolonka tanlanmagan")
    text = to_cyrillic(text) if prof.cyr else text
    if text.strip():
        await _run(prof, "say", text)
    if then_command:
        if text.strip():
            await asyncio.sleep(speak_seconds(text))
        await _run(prof, "command", then_command)


async def speakers(prof: Profile) -> list[dict]:
    async with _locks.setdefault(prof.uid, asyncio.Lock()):
        try:
            return await (await _client(prof)).speakers()
        finally:
            prof.save()


async def diagnose(prof: Profile) -> list[str]:
    async with _locks.setdefault(prof.uid, asyncio.Lock()):
        await reset(prof.uid)
        try:
            return await (await _client(prof)).diagnose()
        finally:
            prof.save()


# ---------------- QR orqali kirish (Home Assistant YandexStation usuli) ----------------
_PWL = "https://passport.yandex.ru/pwl-yandex"


class QrLogin:
    """1) start() -> havola (QR ichida); 2) foydalanuvchi Yandex ilovasida skanerlaydi;
    3) check() -> (x_token, login) yoki None (hali tasdiqlanmagan)."""

    def __init__(self):
        self.s = aiohttp.ClientSession(headers={"User-Agent": UA}, cookie_jar=aiohttp.CookieJar(),
                                       timeout=aiohttp.ClientTimeout(total=20))
        self.headers: dict = {}
        self.auth: dict = {}

    async def close(self):
        await self.s.close()

    async def start(self) -> str:
        async with self.s.get(_PWL) as r:
            body = await r.text()
            status = r.status
        m = re.search(r'__CSRF__ = "([^"]+)', body)
        if status != 200 or not m:
            raise YandexError(f"Yandex kirish sahifasi ochilmadi ({status})")
        self.headers = {"X-CSRF-Token": m.group(1)}
        async with self.s.post(f"{_PWL}/api/passport/auth/password/submit",
                               json={"retpath": "https://passport.yandex.ru/"}, headers=self.headers) as r:
            if r.status != 200:
                raise YandexError(f"Yandex QR seansini boshlab bo'lmadi ({r.status})")
            self.auth = await r.json(content_type=None)
        async with self.s.post(f"{_PWL}/api/passport/auth/magic/code",
                               data={"location_id": "0", "magic_track_id": self.auth.get("track_id", ""),
                                     "track_id": ""}, headers=self.headers) as r:
            if r.status != 200:
                raise YandexError(f"QR kod olinmadi ({r.status})")
            resp = await r.json(content_type=None)
        link = resp.get("link")
        if not link:
            raise YandexError(f"QR kod olinmadi: {str(resp)[:150]}")
        return link

    async def check(self) -> tuple[str, str] | None:
        async with self.s.post(f"{_PWL}/api/passport/auth/magic/code/status",
                               json=self.auth, headers=self.headers) as r:
            if r.status != 200:
                return None
            resp = await r.json(content_type=None)
        if resp.get("state") != "otp_auth_finished":
            return None
        async with self.s.post(f"{_PWL}/api/passport/sessions/get_session",
                               data={"track_id": resp.get("trackId", "")}, headers=self.headers) as r:
            if r.status != 200:
                raise YandexError(f"Yandex sessiyasi olinmadi ({r.status})")
        cookies = "; ".join(f"{c.key}={c.value}" for c in self.s.cookie_jar
                            if str(c["domain"]).endswith("yandex.ru"))
        if "Session_id" not in cookies:
            raise YandexError("Yandex sessiya cookie'sini bermadi — qaytadan urinib ko'ring")
        return await cookies_to_token(cookies)
