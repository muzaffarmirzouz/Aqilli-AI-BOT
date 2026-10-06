import json
import os
import sqlite3
import time

from .config import DB_PATH
from .regions import DEFAULT_REGION

os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
_con = sqlite3.connect(DB_PATH, check_same_thread=False)
_con.row_factory = sqlite3.Row
_con.executescript(
    """
    PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY, name TEXT, region TEXT, notify INTEGER DEFAULT 1,
        active INTEGER DEFAULT 1, created INTEGER);
    CREATE TABLE IF NOT EXISTS chats(
        id INTEGER PRIMARY KEY, title TEXT, username TEXT, region TEXT,
        active INTEGER DEFAULT 1, added_by INTEGER, created INTEGER);
    CREATE TABLE IF NOT EXISTS prayer(
        region TEXT, date TEXT, data TEXT, source TEXT,
        PRIMARY KEY(region, date));
    """
)
_con.commit()
# yangi ustunlar (eski bazalar uchun ham)
for _col in ("theme TEXT", "ad_text TEXT", "ad_contact TEXT", "ad_file TEXT", "logo_file TEXT"):
    try:
        _con.execute(f"ALTER TABLE chats ADD COLUMN {_col}")
    except sqlite3.OperationalError:
        pass
_con.commit()


# ---------- foydalanuvchilar ----------
def upsert_user(uid: int, name: str):
    _con.execute(
        "INSERT INTO users(id,name,created) VALUES(?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET name=excluded.name, active=1",
        (uid, name, int(time.time())),
    )
    _con.commit()


def get_user(uid: int):
    return _con.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()


def set_user(uid: int, **kw):
    for k, v in kw.items():
        _con.execute(f"UPDATE users SET {k}=? WHERE id=?", (v, uid))
    _con.commit()


def notify_users():
    return _con.execute(
        "SELECT id, COALESCE(region, ?) AS region FROM users WHERE notify=1 AND active=1",
        (DEFAULT_REGION,),
    ).fetchall()


def all_active_users():
    return [r["id"] for r in _con.execute("SELECT id FROM users WHERE active=1")]


# ---------- kanallar / guruhlar ----------
def upsert_chat(cid: int, title: str, username: str | None, region: str, added_by: int | None):
    _con.execute(
        "INSERT INTO chats(id,title,username,region,added_by,created) VALUES(?,?,?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET title=excluded.title, username=excluded.username, active=1",
        (cid, title, username, region, added_by, int(time.time())),
    )
    _con.commit()


def set_chat(cid: int, **kw):
    for k, v in kw.items():
        _con.execute(f"UPDATE chats SET {k}=? WHERE id=?", (v, cid))
    _con.commit()


def get_chat(cid: int):
    return _con.execute("SELECT * FROM chats WHERE id=?", (cid,)).fetchone()


def active_chats():
    return _con.execute("SELECT * FROM chats WHERE active=1").fetchall()


def chats_of(uid: int):
    return _con.execute("SELECT * FROM chats WHERE added_by=? AND active=1", (uid,)).fetchall()


# ---------- namoz vaqtlari keshi ----------
def save_prayer(region: str, date: str, data: dict, source: str):
    row = _con.execute("SELECT source FROM prayer WHERE region=? AND date=?", (region, date)).fetchone()
    if row and row["source"] == "manual" and source != "manual":
        return  # qo'lda kiritilgan vaqt ustun turadi
    _con.execute(
        "INSERT OR REPLACE INTO prayer(region,date,data,source) VALUES(?,?,?,?)",
        (region, date, json.dumps(data, ensure_ascii=False), source),
    )
    _con.commit()


def load_prayer(region: str, date: str):
    row = _con.execute("SELECT data, source FROM prayer WHERE region=? AND date=?", (region, date)).fetchone()
    if not row:
        return None
    d = json.loads(row["data"])
    d["source"] = row["source"]
    return d


def stats():
    q = lambda s: _con.execute(s).fetchone()[0]
    return {
        "users": q("SELECT COUNT(*) FROM users"),
        "active": q("SELECT COUNT(*) FROM users WHERE active=1"),
        "notify": q("SELECT COUNT(*) FROM users WHERE active=1 AND notify=1"),
        "chats": q("SELECT COUNT(*) FROM chats WHERE active=1"),
    }


# ---------- kalit-qiymat (oxirgi yuborilgan sana va h.k.) ----------
_con.execute("CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT)")
_con.commit()


def kv_get(k: str):
    r = _con.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
    return r["v"] if r else None


def kv_set(k: str, v: str):
    _con.execute("INSERT OR REPLACE INTO kv(k,v) VALUES(?,?)", (k, v))
    _con.commit()


def used_regions() -> set[str]:
    a = {r[0] for r in _con.execute("SELECT DISTINCT region FROM users WHERE active=1 AND region IS NOT NULL")}
    b = {r[0] for r in _con.execute("SELECT DISTINCT region FROM chats WHERE active=1 AND region IS NOT NULL")}
    return (a | b | {DEFAULT_REGION})
