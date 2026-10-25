import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from bot.config import BOT_TOKEN
from bot.handlers import offer_site_article, router
from bot import igflow, meta, namoz_xabar, site
from yandex import kolonka
from bot.jobs import notify_admins
from bot.jobs import scheduler
from bot.prayer_table import load_dir
from bot.subscribe import SubscribeMiddleware


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN o'rnatilmagan")
    load_dir("prayer_data")  # oylik jadvallarni bazaga yuklash
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.message.outer_middleware(SubscribeMiddleware())
    dp.callback_query.outer_middleware(SubscribeMiddleware())
    dp.include_router(kolonka.router)  # 🔊 Yandex kolonka (matn kutayotganda ustun turadi)
    dp.include_router(namoz_xabar.router)  # 🕌 namoz vaqtida Telegram xabari
    dp.include_router(igflow.router)  # 📩 Instagram avtomatlashtirish buyruqlari (faqat adminlar)
    igflow.setup(bot)
    dp.include_router(router)
    await bot.set_my_commands([
        BotCommand(command="start", description="Bosh menyu"),
        BotCommand(command="namoz", description="Namoz vaqtlari"),
        BotCommand(command="obhavo", description="Ob-havo"),
        BotCommand(command="kurs", description="Valyuta kursi"),
        BotCommand(command="kanal", description="Kanalimga ulash"),
        BotCommand(command="eslatma", description="Namoz vaqtida xabar"),
        BotCommand(command="kolonka", description="Yandex kolonka eslatma"),
    ])
    task = asyncio.create_task(scheduler(bot))
    site_task = asyncio.create_task(site.watcher(bot, offer_site_article, notify_admins))
    ig_task = asyncio.create_task(meta.ig_refresher(lambda t: notify_admins(bot, t)))
    ys_task = asyncio.create_task(kolonka.scheduler(bot))
    pn_task = asyncio.create_task(namoz_xabar.scheduler(bot))
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        task.cancel()
        site_task.cancel()
        ig_task.cancel()
        ys_task.cancel()
        pn_task.cancel()


if __name__ == "__main__":
    asyncio.run(main())
