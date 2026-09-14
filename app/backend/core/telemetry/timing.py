"""Request-local timing primitives with no third-party dependencies."""

from __future__ import annotations

import inspect
import math
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from functools import wraps
from typing import Any

_PHASE_FIELDS = {
    "initialization": "initialization_ms",
    "init.services": "init_services_ms",
    "init.app": "init_app_ms",
    "init.service_imports": "init_service_imports_ms",
    "db.query": "db_query_total_ms",
    "external.http": "external_http_total_ms",
}
_COUNTED_PHASES = {"db.query", "external.http"}


@dataclass(slots=True)
class RequestState:
    started_ns: int
    cold_start: bool
    http_method: str
    http_route: str
    url_path: str
    request_id: str | None
    trace_id: str | None
    aws_request_id: str | None
    status_code: int | None = None
    error_type: str | None = None
    timings_ms: dict[str, float] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    initialization_depth: int = 0


_current_request: ContextVar[RequestState | None] = ContextVar(
    "funcsea_current_request", default=None
)


def activate_request(state: RequestState) -> Token[RequestState | None]:
    return _current_request.set(state)


def deactivate_request(token: Token[RequestState | None]) -> None:
    _current_request.reset(token)


def current_request() -> RequestState | None:
    return _current_request.get()


def record_phase(name: str, duration_ms: float) -> None:
    """Add a duration to the active request; silently ignore invalid input."""

    state = current_request()
    if state is None or name not in _PHASE_FIELDS:
        return
    try:
        duration = float(duration_ms)
    except (TypeError, ValueError):
        return
    if duration < 0 or not math.isfinite(duration):
        return
    if name in _COUNTED_PHASES and state.initialization_depth:
        return
    state.timings_ms[name] = state.timings_ms.get(name, 0.0) + duration
    if name in _COUNTED_PHASES:
        state.counts[name] = state.counts.get(name, 0) + 1


@contextmanager
def measure_phase(name: str) -> Iterator[None]:
    """Measure a fixed, low-cardinality phase for the active request."""

    state = current_request()
    if state is None or name not in _PHASE_FIELDS:
        yield
        return

    is_initialization = name == "initialization" or name.startswith("init.")
    if is_initialization:
        state.initialization_depth += 1
    started_ns = time.perf_counter_ns()
    try:
        yield
    finally:
        elapsed_ms = (time.perf_counter_ns() - started_ns) / 1_000_000
        if is_initialization:
            state.initialization_depth -= 1
        record_phase(name, elapsed_ms)


def timed_phase(name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorate a sync or async initializer without rewriting its body."""

    def decorate(operation: Callable[..., Any]) -> Callable[..., Any]:
        if inspect.iscoroutinefunction(operation):
            @wraps(operation)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                with measure_phase(name):
                    return await operation(*args, **kwargs)

            return async_wrapper

        @wraps(operation)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            with measure_phase(name):
                return operation(*args, **kwargs)

        return sync_wrapper

    return decorate


def timing_fields(state: RequestState, duration_ms: float) -> dict[str, float | int]:
    result: dict[str, float | int] = {
        field_name: round(state.timings_ms.get(phase, 0.0), 3)
        for phase, field_name in _PHASE_FIELDS.items()
    }
    handler_wall_ms = max(
        0.0, duration_ms - state.timings_ms.get("initialization", 0.0)
    )
    result["handler_wall_ms"] = round(handler_wall_ms, 3)
    result["db_query_count"] = state.counts.get("db.query", 0)
    result["external_http_count"] = state.counts.get("external.http", 0)
    attributed_total_ms = state.timings_ms.get(
        "db.query", 0.0
    ) + state.timings_ms.get("external.http", 0.0)
    result["timing_overlap"] = attributed_total_ms > handler_wall_ms
    return result


__all__ = [
    "RequestState",
    "activate_request",
    "current_request",
    "deactivate_request",
    "measure_phase",
    "record_phase",
    "timed_phase",
    "timing_fields",
]
