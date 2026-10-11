"""🕌 «Namoz azoni» — Alisa ko'nikmasi (Yandex Dialogs) uchun webhook.

Kolonka namoz matnini aytgach, bot Alisa'ga «запусти навык <nom>» buyrug'ini yuboradi.
Alisa shu ko'nikmani ochadi, Yandex esa bu webhook'ga so'rov yuboradi — biz azon audiolarini qaytaramiz.
Yandex Plus kerak emas: audio ko'nikmaning o'ziga yuklangan (Dialogs → Resurslar → Ovozlar).

Railway o'zgaruvchilari:
  ALICE_SKILL_ID     — ko'nikma ID'si (Dialogs → ko'nikma → «Общие сведения» → ID навыка)
  ALICE_SKILL_NAME   — faollashtirish nomi (masalan: «намоз азони»)
  AZAN_SOUNDS        — azon audiolari ID'lari, vergul bilan, chalinish tartibida
  AZAN_BOMDOD_SOUNDS — (ixtiyoriy) bomdod azoni ID'lari; bo'lmasa AZAN_SOUNDS
  PORT               — Railway o'zi beradi (webhook manzili: https://<domen>/alice)
"""
import logging
import os

from aiohttp import web

log = logging.getLogger(__name__)

SKILL_ID = os.getenv("ALICE_SKILL_ID", "").strip()
SKILL_NAME = os.getenv("ALICE_SKILL_NAME", "").strip()
SOUNDS = [s.strip() for s in os.getenv("AZAN_SOUNDS", "").split(",") if s.strip()]
BOMDOD_SOUNDS = [s.strip() for s in os.getenv("AZAN_BOMDOD_SOUNDS", "").split(",") if s.strip()]
PATH = "/alice"


def configured() -> bool:
    return bool(SKILL_ID and SKILL_NAME and SOUNDS)


def launch_command(prayer: str | None = None) -> str:
    """Alisa'ga yuboriladigan buyruq. Bomdod uchun alohida azon bo'lsa — ko'nikmaga «бомдод» so'zi uzatiladi."""
    if prayer == "bomdod" and BOMDOD_SOUNDS:
        return f"попроси {SKILL_NAME} бомдод"
    return f"запусти навык {SKILL_NAME}"


def _tts(ids: list[str]) -> str:
    return " ".join(f"<speaker audio='dialogs-upload/{SKILL_ID}/{i}.opus'>" for i in ids)


def build_response(body: dict) -> dict:
    req = body.get("request") or {}
    text = f"{req.get('command', '')} {req.get('original_utterance', '')}".lower()
    version = body.get("version", "1.0")
    if text.strip() == "ping":  # Yandex'ning tekshiruvi
        return {"version": version, "response": {"text": "pong", "end_session": True}}
    ids = BOMDOD_SOUNDS if (BOMDOD_SOUNDS and ("бомдод" in text or "bomdod" in text)) else SOUNDS
    if not ids:
        return {"version": version,
                "response": {"text": "Azon audiosi hali sozlanmagan.", "end_session": True}}
    return {"version": version,
            "response": {"text": "Azon", "tts": _tts(ids), "end_session": True}}


async def _handle(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "bad json"}, status=400)
    return web.json_response(build_response(body))


async def _health(_request: web.Request) -> web.Response:
    return web.Response(text="ok" if configured() else "not configured")


async def serve():
    """Webhook serverini ishga tushiradi (bot bilan bir jarayonda). PORT bo'lmasa — ishga tushmaydi."""
    port = os.getenv("PORT")
    if not port:
        log.info("alice skill: PORT yo'q — webhook ishga tushmadi")
        return
    app = web.Application()
    app.router.add_post(PATH, _handle)
    app.router.add_get("/", _health)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(port)).start()
    log.info("alice skill webhook: :%s%s (sozlangan: %s)", port, PATH, configured())
