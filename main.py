import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from bot.config import BOT_TOKEN
from bot.handlers import router
from bot.jobs import scheduler


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN o'rnatilmagan")
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)
    await bot.set_my_commands([
        BotCommand(command="start", description="Bosh menyu"),
        BotCommand(command="namoz", description="Namoz vaqtlari"),
        BotCommand(command="obhavo", description="Ob-havo"),
        BotCommand(command="kurs", description="Valyuta kursi"),
        BotCommand(command="hudud", description="Hududni o'zgartirish"),
        BotCommand(command="kanal", description="Kanalimga ulash"),
    ])
    task = asyncio.create_task(scheduler(bot))
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        task.cancel()


if __name__ == "__main__":
    asyncio.run(main())
