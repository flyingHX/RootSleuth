"""Emit one bounded request summary without telemetry network I/O."""

from __future__ import annotations

import functools
import json
import os
import re
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .timing import RequestState, activate_request, deactivate_request, timing_fields

_FUNCTION_IDENTITY = re.compile(
    r"-app-([0-9a-fA-F]{32})-(dev|prod)(?:-(v[0-9]+))?$"
)
_TRACEPARENT = re.compile(
    r"^[0-9a-fA-F]{2}-([0-9a-fA-F]{32})-[0-9a-fA-F]{16}-[0-9a-fA-F]{2}$"
)
_FALSE_VALUES = {"0", "false", "no", "off"}
_cold_start = True


@dataclass(slots=True)
class _RequestScope:
    state: RequestState
    token: Any


def _is_enabled() -> bool:
    configured = os.getenv(
        "FUNCSEA_TELEMETRY_ENABLED",
        os.getenv("FUNCSEA_REQUEST_SUMMARY_ENABLED", "true"),
    )
    return configured.strip().lower() not in _FALSE_VALUES


def _safe_correlation(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized or len(normalized) > 128:
        return None
    if any(ord(character) < 32 or ord(character) == 127 for character in normalized):
        return None
    return normalized


def _normalize_headers(headers: Any) -> dict[str, str]:
    return {
        str(key).lower(): str(value)
        for key, value in (headers or {}).items()
        if value is not None
    }


def _trace_id(headers: dict[str, str]) -> str | None:
    traceparent = headers.get("traceparent", "").split(",", 1)[0].strip()
    match = _TRACEPARENT.fullmatch(traceparent)
    if match and match.group(1) != "0" * 32:
        return match.group(1).lower()
    return _safe_correlation(headers.get("x-trace-id") or headers.get("trace-id"))


def _method(event: dict[str, Any]) -> str:
    request_context = event.get("requestContext") or {}
    http_context = request_context.get("http") or {}
    return str(http_context.get("method") or event.get("httpMethod") or "UNKNOWN")


def _path(event: dict[str, Any]) -> str:
    return str(event.get("rawPath") or event.get("path") or "/")


def _route(event: dict[str, Any], method: str, path: str) -> str:
    request_context = event.get("requestContext") or {}
    route_key = event.get("routeKey") or request_context.get("routeKey")
    if route_key and route_key != "$default":
        prefix = f"{method} "
        route = str(route_key)
        return route.removeprefix(prefix)
    return path


def _status_code(response: Any) -> int | None:
    if not isinstance(response, dict):
        return None
    try:
        return int(response.get("statusCode"))
    except (TypeError, ValueError):
        return None


def _is_excluded(method: str, path: str) -> bool:
    return method.upper() == "OPTIONS" or not path.startswith("/api/v1/")


def _bounded_text(value: Any, limit: int) -> str:
    return str(value)[:limit]


def _service_attributes() -> dict[str, str]:
    function_name = os.getenv("AWS_LAMBDA_FUNCTION_NAME", "")
    attributes = {
        "function_name": function_name,
        "app_id": os.getenv("FUNCSEA_APP_ID") or os.getenv("OIDC_CLIENT_ID", ""),
        "environment": os.getenv("FUNCSEA_ENVIRONMENT")
        or os.getenv("ENVIRONMENT", ""),
        "service_version": os.getenv("FUNCSEA_SERVICE_VERSION", ""),
    }
    identity = _FUNCTION_IDENTITY.search(function_name)
    if identity:
        attributes["app_id"] = attributes["app_id"] or identity.group(1).lower()
        attributes["environment"] = attributes["environment"] or identity.group(2)
        attributes["service_version"] = (
            attributes["service_version"] or identity.group(3) or "latest"
        )
    return attributes


def _emit(summary: dict[str, Any]) -> None:
    try:
        sys.stdout.write(
            json.dumps(summary, ensure_ascii=False, separators=(",", ":")) + "\n"
        )
        # Lambda may freeze the runtime immediately after the handler returns.
        # Flush only the local stdout pipe; no telemetry network I/O happens here.
        sys.stdout.flush()
    except Exception:  # noqa: BLE001 - stdout diagnostics are strictly fail-open
        return


def _start_request(
    *,
    method: str,
    path: str,
    route: str,
    headers: Any,
    aws_request_id: Any = None,
    request_id_fallback: Any = None,
) -> _RequestScope | None:
    global _cold_start

    if not _is_enabled():
        return None

    is_cold_start = _cold_start
    _cold_start = False
    if _is_excluded(method, path):
        return None

    normalized_headers = _normalize_headers(headers)
    state = RequestState(
        started_ns=time.perf_counter_ns(),
        cold_start=is_cold_start,
        http_method=_bounded_text(method, 16),
        http_route=_bounded_text(route, 512),
        url_path=_bounded_text(path, 2048),
        request_id=_safe_correlation(
            normalized_headers.get("x-kong-request-id")
            or normalized_headers.get("x-request-id")
            or request_id_fallback
        ),
        trace_id=_trace_id(normalized_headers),
        aws_request_id=_safe_correlation(aws_request_id),
    )
    return _RequestScope(state=state, token=activate_request(state))


def _finish_request(
    scope: _RequestScope,
    *,
    status_code: int | None,
    error_type: str | None = None,
) -> None:
    state = scope.state
    duration_ms = (time.perf_counter_ns() - state.started_ns) / 1_000_000
    state.status_code = status_code if status_code is not None else state.status_code
    state.error_type = error_type or state.error_type
    try:
        summary: dict[str, Any] = {
            "event_name": "funcsea.request.summary",
            "schema_version": 1,
            **_service_attributes(),
            "request_id": state.request_id,
            "trace_id": state.trace_id,
            "aws_request_id": state.aws_request_id,
            "http_method": state.http_method,
            "http_route": state.http_route,
            "url_path": state.url_path,
            "status_code": state.status_code,
            "duration_ms": round(duration_ms, 3),
            "cold_start": state.cold_start,
            "backend_initialized_this_request": bool(
                state.timings_ms.get("initialization", 0.0)
            ),
            **timing_fields(state, duration_ms),
        }
        if state.error_type:
            summary["error_type"] = state.error_type
        _emit(summary)
    except Exception:  # noqa: BLE001 - diagnostics are strictly fail-open
        return
    finally:
        try:
            deactivate_request(scope.token)
        except Exception:  # noqa: BLE001,S110 - diagnostics are strictly fail-open
            pass


def summarize_lambda_handler(
    handler: Callable[[dict[str, Any], Any], Any],
) -> Callable[[dict[str, Any], Any], Any]:
    """Wrap a Lambda handler while preserving its response and exceptions."""

    @functools.wraps(handler)
    def wrapper(event: dict[str, Any], context: Any) -> Any:
        try:
            method = _method(event)
            path = _path(event)
            request_context = event.get("requestContext") or {}
            scope = _start_request(
                method=method,
                path=path,
                route=_route(event, method, path),
                headers=event.get("headers"),
                aws_request_id=getattr(context, "aws_request_id", None),
                request_id_fallback=request_context.get("requestId"),
            )
        except Exception:  # noqa: BLE001 - diagnostics are strictly fail-open
            return handler(event, context)
        if scope is None:
            return handler(event, context)

        response: Any = None
        error_type: str | None = None
        try:
            response = handler(event, context)
            return response
        except Exception as error:
            error_type = type(error).__name__
            raise
        finally:
            _finish_request(
                scope,
                status_code=_status_code(response),
                error_type=error_type,
            )

    return wrapper


__all__ = ["summarize_lambda_handler"]
