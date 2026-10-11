"""Har bir foydalanuvchining Yandex kolonka sozlamalari (bot bazasidagi ys_users jadvali)."""
import json
import time
from dataclasses import asdict, dataclass, field

from bot import db

_con = db._con
_con.execute(
    """CREATE TABLE IF NOT EXISTS ys_users(
        uid INTEGER PRIMARY KEY,
        x_token TEXT, login TEXT,
        device_id TEXT, device_name TEXT,
        active INTEGER DEFAULT 0,
        cyr INTEGER DEFAULT 0,
        mode TEXT, scenario TEXT, scenario_name TEXT, csrf_url TEXT,
        texts TEXT, enabled TEXT,
        last_err TEXT, last_err_day TEXT,
        created INTEGER, updated INTEGER)"""
)
_con.commit()
for _col in ("scenario_name TEXT", "azan INTEGER DEFAULT 0", "azan_cmd TEXT"):  # eski jadvalga yangi ustunlar
    try:
        _con.execute(f"ALTER TABLE ys_users ADD COLUMN {_col}")
        _con.commit()
    except Exception:
        pass


@dataclass
class Profile:
    uid: int
    x_token: str = ""
    login: str = ""
    device_id: str = ""
    device_name: str = ""
    active: int = 0
    cyr: int = 0
    mode: str = ""
    scenario: str = ""
    scenario_name: str = ""
    csrf_url: str = ""
    azan: int = 0          # matndan keyin azon (Alisa'ga buyruq) yoqilganmi
    azan_cmd: str = ""     # Alisa'ga yuboriladigan buyruq (bo'sh — standart)
    texts: dict = field(default_factory=dict)
    enabled: dict = field(default_factory=dict)
    last_err: str = ""
    last_err_day: str = ""
    created: int = 0
    updated: int = 0

    @property
    def connected(self) -> bool:
        return bool(self.x_token)

    @property
    def ready(self) -> bool:
        return bool(self.x_token and self.device_id)

    def save(self):
        save(self)


def _row_to_profile(r) -> Profile:
    d = dict(r)
    for k in ("texts", "enabled"):
        try:
            d[k] = json.loads(d.get(k) or "{}")
        except ValueError:
            d[k] = {}
    for k, v in list(d.items()):
        if v is None:
            d[k] = 0 if k in ("active", "cyr", "azan", "created", "updated") else ""
    return Profile(**d)


def get(uid: int) -> Profile:
    r = _con.execute("SELECT * FROM ys_users WHERE uid=?", (uid,)).fetchone()
    return _row_to_profile(r) if r else Profile(uid=uid)


def save(p: Profile):
    d = asdict(p)
    d["texts"] = json.dumps(p.texts, ensure_ascii=False)
    d["enabled"] = json.dumps(p.enabled)
    d["updated"] = int(time.time())
    if not d["created"]:
        d["created"] = d["updated"]
    cols = ",".join(d)
    marks = ",".join("?" * len(d))
    upd = ",".join(f"{k}=excluded.{k}" for k in d if k != "uid")
    _con.execute(f"INSERT INTO ys_users({cols}) VALUES({marks}) ON CONFLICT(uid) DO UPDATE SET {upd}",
                 tuple(d.values()))
    _con.commit()


def delete(uid: int):
    """Foydalanuvchi uzganda: token va barcha sozlamalari o'chiriladi."""
    _con.execute("DELETE FROM ys_users WHERE uid=?", (uid,))
    _con.commit()


def active_profiles() -> list[Profile]:
    rows = _con.execute(
        "SELECT * FROM ys_users WHERE active=1 AND x_token<>'' AND device_id<>''").fetchall()
    return [_row_to_profile(r) for r in rows]


def stats() -> dict:
    q = lambda s: _con.execute(s).fetchone()[0]
    return {
        "connected": q("SELECT COUNT(*) FROM ys_users WHERE x_token<>''"),
        "active": q("SELECT COUNT(*) FROM ys_users WHERE active=1 AND x_token<>'' AND device_id<>''"),
    }


def migrate_legacy(uid: int):
    """Bitta-admin versiyasidagi (kv) sozlamalarni shu admin profiliga ko'chiradi — bir marta."""
    tok = db.kv_get("ys_xtoken")
    if not tok or _con.execute("SELECT 1 FROM ys_users WHERE uid=?", (uid,)).fetchone():
        return
    keys = ("bomdod", "quyosh", "peshin", "asr", "shom", "xufton")
    p = Profile(
        uid=uid, x_token=tok, login=db.kv_get("ys_login") or "",
        device_id=db.kv_get("ys_device") or "", device_name=db.kv_get("ys_device_name") or "",
        active=1 if db.kv_get("ys_on") == "1" else 0,
        cyr=0 if db.kv_get("ys_cyr") == "0" else 1,
        mode=(db.kv_get("ys_mode") or "").replace("scenario", ""), scenario=db.kv_get("ys_scenario") or "",
        csrf_url=db.kv_get("ys_csrf_url") or "",
        texts={k: v for k in keys if (v := db.kv_get(f"ys_text_{k}"))},
        enabled={k: db.kv_get(f"ys_en_{k}") == "1" for k in keys if db.kv_get(f"ys_en_{k}") is not None},
    )
    save(p)
    for k in ("ys_xtoken", "ys_cookie", "ys_on"):
        db.kv_set(k, "")
