import asyncio
import logging
from logging import Logger
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from .config import settings
from .db import DB
from .handlers import router
from .middlewares import RateLimitMiddleware
from .payments import GROUP_PLANS
from .texts import ERROR_MESSAGES, BOT_COMMANDS


def setup_logging() -> Logger:
    logger = logging.getLogger()
    logger.setLevel(settings.log_level)

    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    if not logger.handlers:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

        try:
            log_dir = Path("logs")
            log_dir.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(log_dir / "resolver.log")
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        except OSError:
            logger.warning("Could not write logs/resolver.log; continuing with console logging only")

    return logging.getLogger(__name__)


async def main() -> None:
    logger = setup_logging()
    v2_enabled = settings.feature_v2_personal or settings.feature_v2_groups
    logger.info("FEATURES: v2_enabled=%s", v2_enabled)
    group_plan_summary = ", ".join(
        f"{GROUP_PLANS[plan_id].name}={GROUP_PLANS[plan_id].stars} Stars"
        for plan_id in ("group_monthly", "group_yearly", "group_lifetime")
        if plan_id in GROUP_PLANS
    )
    logger.info("Group plan prices: %s", group_plan_summary or "none")

    if not settings.bot_token_valid:
        logger.error("BOT_TOKEN is missing. Update your .env file.")
        print(ERROR_MESSAGES["config_missing"])
        return

    db = DB(settings.db_path)
    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)

    dp["db"] = db

    dp.message.middleware(RateLimitMiddleware())

    dp.include_router(router)

    try:
        bot_info = await bot.get_me()
        logger.info("Starting The Resolver bot: @%s", bot_info.username)
    except Exception as exc:
        logger.error("Failed to fetch bot info: %s", exc)

    try:
        await bot.set_my_commands(
            [BotCommand(command=command, description=description) for command, description in BOT_COMMANDS]
        )
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    except Exception as exc:
        logger.error("Bot failed: %s", exc)
        raise
    finally:
        await bot.session.close()
        logger.info("Bot stopped")


if __name__ == "__main__":
    asyncio.run(main())
