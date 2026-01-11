import logging
import time
import traceback
from typing import Dict, Any, Callable, Awaitable, Union

from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery
from cachetools import TTLCache

from .config import settings
from .texts import ERROR_MESSAGES

logger = logging.getLogger(__name__)


class ErrorHandlingMiddleware(BaseMiddleware):
    """Catch and log exceptions with safe fallbacks."""

    async def __call__(
        self,
        handler: Callable[[Union[Message, CallbackQuery], Dict[str, Any]], Awaitable[Any]],
        event: Union[Message, CallbackQuery],
        data: Dict[str, Any],
    ) -> Any:
        try:
            return await handler(event, data)
        except Exception:
            logger.error("Unhandled exception in handler:\n%s", traceback.format_exc())
            if isinstance(event, CallbackQuery):
                try:
                    await event.answer()
                except Exception:
                    logger.warning("Failed to answer callback after error.")
                if event.message:
                    await event.message.answer(ERROR_MESSAGES["generic"])
                return None
            if isinstance(event, Message):
                await event.answer(ERROR_MESSAGES["generic"])
                return None
            return None


class CallbackLoggingMiddleware(BaseMiddleware):
    """Log callback data for routing diagnostics."""

    async def __call__(
        self,
        handler: Callable[[CallbackQuery, Dict[str, Any]], Awaitable[Any]],
        event: CallbackQuery,
        data: Dict[str, Any],
    ) -> Any:
        if event.message and event.from_user:
            logger.info(
                "Callback received: data=%s chat_id=%s user_id=%s",
                event.data,
                event.message.chat.id,
                event.from_user.id,
            )
        return await handler(event, data)


class RateLimitMiddleware(BaseMiddleware):
    """Rate limiting middleware."""

    def __init__(self) -> None:
        self.user_cache = TTLCache(maxsize=10000, ttl=60)

    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: Dict[str, Any],
    ) -> Any:
        if not event.from_user:
            return await handler(event, data)

        user_id = event.from_user.id
        current_time = time.time()
        user_data = self.user_cache.get(user_id, {"count": 0, "first_request": current_time})

        if user_data["count"] >= settings.rate_limit_per_user:
            elapsed = current_time - user_data["first_request"]
            if elapsed < 60:
                await event.answer(ERROR_MESSAGES["rate_limit"])
                return None
            user_data = {"count": 1, "first_request": current_time}
        else:
            user_data["count"] += 1

        self.user_cache[user_id] = user_data
        return await handler(event, data)
