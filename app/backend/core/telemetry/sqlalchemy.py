"""Optional SQLAlchemy query timing; connection-pool time remains unclassified."""

from __future__ import annotations

import time
from typing import Any

from .timing import current_request, record_phase

_INSTRUMENTED = "_funcsea_query_timing_instrumented"
_STARTED_NS = "_funcsea_query_started_ns"


def instrument_sqlalchemy(engine: Any) -> None:
    """Attach idempotent, fail-open query hooks to an Engine or AsyncEngine."""

    sync_engine = getattr(engine, "sync_engine", engine)
    if getattr(sync_engine, _INSTRUMENTED, False):
        return

    try:
        from sqlalchemy import event

        def before_cursor_execute(
            _connection: Any,
            _cursor: Any,
            _statement: Any,
            _parameters: Any,
            context: Any,
            _executemany: Any,
        ) -> None:
            try:
                if current_request() is not None:
                    setattr(context, _STARTED_NS, time.perf_counter_ns())
            except Exception:  # noqa: BLE001,S110 - query behavior wins
                pass

        def finish(context: Any) -> None:
            try:
                started_ns = getattr(context, _STARTED_NS, None)
                if started_ns is not None:
                    record_phase(
                        "db.query", (time.perf_counter_ns() - started_ns) / 1_000_000
                    )
                    delattr(context, _STARTED_NS)
            except Exception:  # noqa: BLE001,S110 - query behavior wins
                pass

        def after_cursor_execute(
            _connection: Any,
            _cursor: Any,
            _statement: Any,
            _parameters: Any,
            context: Any,
            _executemany: Any,
        ) -> None:
            finish(context)

        def handle_error(exception_context: Any) -> None:
            finish(getattr(exception_context, "execution_context", None))

        event.listen(sync_engine, "before_cursor_execute", before_cursor_execute)
        event.listen(sync_engine, "after_cursor_execute", after_cursor_execute)
        event.listen(sync_engine, "handle_error", handle_error)
        setattr(sync_engine, _INSTRUMENTED, True)
    except Exception:  # noqa: BLE001,S110 - optional instrumentation
        pass


__all__ = ["instrument_sqlalchemy"]
