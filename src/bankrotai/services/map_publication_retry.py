"""Keep MapDataset dirty signal after an asynchronous build failure.

The minute watchdog consumes Redis dirty-generation before queueing the build.
If the build raises or refuses promotion, preserve the signal for a later
bounded retry while leaving the previously published dataset active.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any


def build_with_retry_signal(
    build: Callable[..., dict[str, Any]],
    session_factory: Any,
    redis_client: Any,
    mark_dirty: Callable[[Any], None],
) -> dict[str, Any]:
    try:
        result = build(session_factory)
    except Exception:
        mark_dirty(redis_client)
        raise
    if result.get("promotion_status") != "published":
        mark_dirty(redis_client)
    return result
