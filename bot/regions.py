# Hududlar: kalit -> (ko'rinadigan nom, islomapi.uz dagi nom, kenglik, uzunlik)
# islomapi.uz ma'lumotlari islom.uz (O'zbekiston musulmonlari idorasi) taqvimidan olinadi.
REGIONS = {
    "namangan":  ("Namangan",  "Namangan",  40.9983, 71.6726),
    "toshkent":  ("Toshkent",  "Toshkent",  41.2995, 69.2401),
    "andijon":   ("Andijon",   "Andijon",   40.7821, 72.3442),
    "fargona":   ("Farg'ona",  "Farg'ona",  40.3894, 71.7843),
    "qoqon":     ("Qo'qon",    "Qo'qon",    40.5286, 70.9425),
    "margilon":  ("Marg'ilon", "Marg'ilon", 40.4711, 71.7247),
    "samarqand": ("Samarqand", "Samarqand", 39.6542, 66.9597),
    "buxoro":    ("Buxoro",    "Buxoro",    39.7747, 64.4286),
    "navoiy":    ("Navoiy",    "Navoiy",    40.0844, 65.3792),
    "jizzax":    ("Jizzax",    "Jizzax",    40.1158, 67.8422),
    "guliston":  ("Guliston",  "Guliston",  40.4897, 68.7842),
    "qarshi":    ("Qarshi",    "Qarshi",    38.8606, 65.7891),
    "termiz":    ("Termiz",    "Termiz",    37.2242, 67.2783),
    "urganch":   ("Urganch",   "Urganch",   41.5500, 60.6333),
    "xiva":      ("Xiva",      "Xiva",      41.3783, 60.3639),
    "nukus":     ("Nukus",     "Nukus",     42.4600, 59.6100),
}

DEFAULT_REGION = "namangan"


def name(key: str) -> str:
    return REGIONS.get(key, REGIONS[DEFAULT_REGION])[0]
