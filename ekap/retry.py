"""Sınırlı retry/backoff. Sonsuz döngü yok."""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, TypeVar

T = TypeVar("T")
log = logging.getLogger("ekap.retry")


async def with_retry(
    factory: Callable[[], Awaitable[T]],
    *,
    attempts: int = 3,
    base_delay: float = 1.5,
    label: str = "işlem",
) -> T:
    attempts = max(1, int(attempts))
    last: BaseException | None = None
    for index in range(1, attempts + 1):
        try:
            return await factory()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            last = exc
            if getattr(exc, "retryable", True) is False:
                raise
            if index >= attempts:
                break
            delay = base_delay * (2 ** (index - 1))
            log.warning(
                "%s başarısız (%s: %s); %.1fs sonra yeniden denenecek (%s/%s)",
                label,
                type(exc).__name__,
                exc,
                delay,
                index,
                attempts,
            )
            await asyncio.sleep(delay)
    assert last is not None
    raise last
