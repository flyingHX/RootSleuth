"""Fail-open timing for an existing async HTTP operation."""

from __future__ import annotations

import time
from collections.abc import Awaitable
from typing import TypeVar

from .timing import current_request, record_phase

_T = TypeVar("_T")


async def observe_external_http(operation: Awaitable[_T]) -> _T:
    """Observe an awaitable without changing its client, result, or exception."""

    if current_request() is None:
        return await operation

    started_ns = time.perf_counter_ns()
    try:
        return await operation
    finally:
        try:
            record_phase(
                "external.http", (time.perf_counter_ns() - started_ns) / 1_000_000
            )
        except Exception:  # noqa: BLE001,S110 - preserve the business exception
            pass


__all__ = ["observe_external_http"]
