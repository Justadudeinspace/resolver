import time
from typing import Dict, Any, Callable, Awaitable

from aiogram import BaseMiddleware
from aiogram.types import Message
from cachetools import TTLCache

from .config import settings
from .texts import ERROR_MESSAGES


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
