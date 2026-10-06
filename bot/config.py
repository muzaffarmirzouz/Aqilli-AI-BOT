import os
from zoneinfo import ZoneInfo

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x}
DB_PATH = os.getenv("DB_PATH", "data/bot.db")
TZ = ZoneInfo(os.getenv("TZ_NAME", "Asia/Tashkent"))

# Kechki yuborish vaqti (HH:MM) va undan oldingi tekshiruv (daqiqa)
SEND_AT = os.getenv("SEND_AT", "21:00")
PRECHECK_MIN = int(os.getenv("PRECHECK_MIN", "30"))
# Jadval tugashidan necha kun oldin "yangilang" eslatmasi kelsin
REMIND_DAYS = int(os.getenv("REMIND_DAYS", "5"))

# Takbir daqiqalari: bomdod, quyosh, peshin, asr, shom, xufton. 0 = ko'rsatilmaydi.
TAKBIR = [int(x) for x in os.getenv("TAKBIR", "40,0,10,10,10,10").split(",")]

# Posterdagi oyat
AYAH_TEXT = os.getenv(
    "AYAH_TEXT",
    "…namozni to'liq ado eting. Albatta, namoz mo'minlarga vaqtida farz qilingandir.",
)
AYAH_SOURCE = os.getenv("AYAH_SOURCE", "Niso surasi, 103-oyat")

FONT_DIR = os.getenv("FONT_DIR", "fonts")

PRAYER_KEYS = ["bomdod", "quyosh", "peshin", "asr", "shom", "xufton"]
