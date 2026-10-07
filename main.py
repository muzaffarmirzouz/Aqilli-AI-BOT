import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from bot.config import BOT_TOKEN
from bot.handlers import offer_site_article, router
from bot import meta, site
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
    dp.include_router(router)
    await bot.set_my_commands([
        BotCommand(command="start", description="Bosh menyu"),
        BotCommand(command="namoz", description="Namoz vaqtlari"),
        BotCommand(command="obhavo", description="Ob-havo"),
        BotCommand(command="kurs", description="Valyuta kursi"),
        BotCommand(command="kanal", description="Kanalimga ulash"),
    ])
    task = asyncio.create_task(scheduler(bot))
    site_task = asyncio.create_task(site.watcher(bot, offer_site_article, notify_admins))
    ig_task = asyncio.create_task(meta.ig_refresher(lambda t: notify_admins(bot, t)))
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        task.cancel()
        site_task.cancel()
        ig_task.cancel()


if __name__ == "__main__":
    asyncio.run(main())
