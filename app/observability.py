"""Observability utilities — query timing decorator.

Apply @timed to any method to log its name, duration, and optional row count.
"""

from __future__ import annotations

import logging
import time
from functools import wraps
from typing import Any, Callable, TypeVar

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


def timed(logger: logging.Logger | None = None) -> Callable[[F], F]:
    """Decorator that logs method name, duration, and row count.

    If the return value is a dict with a ``count`` key, or a list/tuple,
    the length is logged as ``rows=N``.

    Usage::

        @timed()
        def fleet_summary(self, ...) -> ...:
            ...
    """
    _logger = logger or logging.getLogger(__name__)

    def decorator(func: F) -> F:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            start = time.perf_counter()
            result = func(*args, **kwargs)
            elapsed = time.perf_counter() - start
            rows = ""
            if isinstance(result, dict):
                n = result.get("count") or result.get("rows")
                if n is not None:
                    rows = f" rows={n}"
            elif isinstance(result, (list, tuple)):
                rows = f" rows={len(result)}"
            _logger.info("timed_method=%s elapsed_ms=%d%s", func.__qualname__, int(elapsed * 1000), rows)
            return result

        return wrapper  # type: ignore[return-value]

    return decorator
