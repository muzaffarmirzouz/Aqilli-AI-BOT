"""📊 Admin uchun umumiy statistika: /stat, /statistika va Admin panel → «📊 Statistika»."""
from datetime import datetime

from aiogram import Bot
from aiogram.enums import ChatMemberStatus

from . import db
from .config import REQUIRED_CHANNEL, TZ


def _q(sql: str, *args) -> int:
    try:
        return db._con.execute(sql, args).fetchone()[0] or 0
    except Exception:
        return 0  # jadval/ustun hali yo'q


def _day_start(days_ago: int = 0) -> int:
    n = datetime.now(TZ)
    start = n.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(start.timestamp()) - days_ago * 86400


async def channel_check(bot: Bot) -> str:
    if not REQUIRED_CHANNEL:
        return "🔓 Majburiy obuna: o'chiq"
    try:
        me = await bot.get_me()
        m = await bot.get_chat_member(REQUIRED_CHANNEL, me.id)
        if m.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR):
            return f"🔒 Majburiy obuna: {REQUIRED_CHANNEL} — ✅ ishlayapti (bot kanalda admin)"
        return (f"🔒 Majburiy obuna: {REQUIRED_CHANNEL} — ❌ ISHLAMAYAPTI: bot kanalda admin emas, "
                "hamma obunasiz kiryapti. Botni kanalga admin qiling.")
    except Exception as e:
        return f"🔒 Majburiy obuna: {REQUIRED_CHANNEL} — ⚠️ tekshirib bo'lmadi ({e})"


async def report(bot: Bot) -> str:
    ys_connected = _q("SELECT COUNT(*) FROM ys_users WHERE x_token<>''")
    ys_active = _q("SELECT COUNT(*) FROM ys_users WHERE active=1 AND x_token<>'' AND device_id<>''")
    today, d7, d30 = _day_start(0), _day_start(6), _day_start(29)
    total = _q("SELECT COUNT(*) FROM users")
    alive = _q("SELECT COUNT(*) FROM users WHERE active=1")
    blocked = total - alive
    tracked_since = _q("SELECT MIN(last_seen) FROM users WHERE last_seen IS NOT NULL")
    lines = [
        "📊 <b>Bot statistikasi</b>",
        f"🕐 {datetime.now(TZ).strftime('%d.%m.%Y %H:%M')}",
        "",
        "<b>👥 Foydalanuvchilar</b>",
        f"Jami: <b>{total}</b>",
        f"Botni ishlatayotgan (bloklamagan): <b>{alive}</b>" + (f" · bloklagan: {blocked}" if blocked else ""),
        "",
        "<b>🟢 Botdan foydalanganlar</b>",
        f"Bugun: <b>{_q('SELECT COUNT(*) FROM users WHERE last_seen>=?', today)}</b>",
        f"Oxirgi 7 kun: <b>{_q('SELECT COUNT(*) FROM users WHERE last_seen>=?', d7)}</b>",
        f"Oxirgi 30 kun: <b>{_q('SELECT COUNT(*) FROM users WHERE last_seen>=?', d30)}</b>",
    ]
    if tracked_since:
        since = datetime.fromtimestamp(tracked_since, TZ).strftime("%d.%m.%Y")
        lines.append(f"<i>(faollik {since} dan beri hisoblanmoqda)</i>")
    lines += [
        "",
        "<b>🆕 Yangi qo'shilganlar</b>",
        f"Bugun: <b>{_q('SELECT COUNT(*) FROM users WHERE created>=?', today)}</b> · "
        f"7 kun: <b>{_q('SELECT COUNT(*) FROM users WHERE created>=?', d7)}</b> · "
        f"30 kun: <b>{_q('SELECT COUNT(*) FROM users WHERE created>=?', d30)}</b>",
        "",
        "<b>⚙️ Xizmatlar</b>",
        f"🔔 Kechki xabar (21:00): <b>{_q('SELECT COUNT(*) FROM users WHERE active=1 AND notify=1')}</b>",
        f"🕌 Namoz vaqtida xabar: <b>{_q('SELECT COUNT(*) FROM users WHERE active=1 AND pn=1')}</b>"
        f" (azon bilan: {_q('SELECT COUNT(*) FROM users WHERE active=1 AND pn=1 AND pn_audio=1')})",
        f"🔊 Kolonka: ulangan <b>{ys_connected}</b>, yoqilgan <b>{ys_active}</b>",
        f"📢 Ulangan kanal/guruhlar: <b>{_q('SELECT COUNT(*) FROM chats WHERE active=1')}</b>",
        "",
        await channel_check(bot),
    ]
    return "\n".join(lines)
