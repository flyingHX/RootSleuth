"""Minimal ASGI middleware for request summaries."""

from __future__ import annotations

from typing import Any

from .request_summary import _finish_request, _start_request
from .timing import current_request


def _headers(scope: dict[str, Any]) -> dict[str, str]:
    return {
        key.decode("latin-1").lower(): value.decode("latin-1")
        for key, value in scope.get("headers", [])
    }


class RequestSummaryMiddleware:
    """Emit one summary in sandbox mode, or enrich the Lambda-owned summary."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        state = current_request()
        owner = None
        if state is None:
            try:
                owner = _start_request(
                    method=str(scope.get("method") or "UNKNOWN"),
                    path=str(scope.get("path") or "/"),
                    route=str(scope.get("path") or "/"),
                    headers=_headers(scope),
                )
            except Exception:  # noqa: BLE001 - diagnostics are strictly fail-open
                owner = None
            state = owner.state if owner else None

        status_code: int | None = None

        async def capture_status(message: dict[str, Any]) -> None:
            nonlocal status_code
            if message.get("type") == "http.response.start":
                status_code = int(message.get("status", 0)) or None
            await send(message)

        error_type: str | None = None
        try:
            await self.app(scope, receive, capture_status)
        except Exception as error:
            error_type = type(error).__name__
            if status_code is None:
                status_code = 500
            raise
        finally:
            if state is not None:
                try:
                    route = getattr(scope.get("route"), "path", None)
                    if route:
                        state.http_route = str(route)[:512]
                    elif status_code is not None and 300 <= status_code < 400:
                        state.http_route = "__redirect__"
                    else:
                        state.http_route = "__unmatched__"
                    state.url_path = str(scope.get("path") or state.url_path)[:2048]
                    state.status_code = status_code
                    state.error_type = error_type
                except Exception:  # noqa: BLE001,S110 - diagnostics are strictly fail-open
                    pass
            if owner is not None:
                _finish_request(owner, status_code=status_code, error_type=error_type)


__all__ = ["RequestSummaryMiddleware"]
