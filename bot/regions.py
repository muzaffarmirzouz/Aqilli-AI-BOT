# Hududlar: kalit -> (ko'rinadigan nom, islomapi.uz dagi nom, kenglik, uzunlik)
# islomapi.uz ma'lumotlari islom.uz (O'zbekiston musulmonlari idorasi) taqvimidan olinadi.
REGIONS = {
    "namangan": ("Namangan", "Namangan", 40.9983, 71.6726),
    # Boshqa hududlar qo'shilganda shu yerga yoziladi va prayer_data/ ga jadvali qo'yiladi.
}

DEFAULT_REGION = "namangan"


def name(key: str) -> str:
    return REGIONS.get(key, REGIONS[DEFAULT_REGION])[0]
