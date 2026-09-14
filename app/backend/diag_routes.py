"""临时诊断脚本：确认路由注册与模型主键状态（诊断后删除）。"""
import importlib
import os
import pkgutil
import sys

print("CWD:", os.getcwd())
print("models/events.py exists:", os.path.exists("models/events.py"))
print("routers/events.py exists:", os.path.exists("routers/events.py"))

import main

print("MAIN_FILE:", main.__file__)
routes = main.app.routes
print("TOTAL_ROUTES:", len(routes))
api_paths = sorted(
    {getattr(r, "path", "?") for r in routes if str(getattr(r, "path", "")).startswith("/api")}
)
print("API_ROUTES:", len(api_paths))
for p in api_paths[:25]:
    print("  API", p)

print("---- router module discovery state after import main ----")
import routers  # noqa: E402

for m in pkgutil.walk_packages(routers.__path__, "routers."):
    cached = m.name in sys.modules
    r_attr = getattr(sys.modules.get(m.name), "router", None) if cached else None
    print(
        "DISCOVERY",
        m.name,
        "cached=",
        cached,
        "router=",
        type(r_attr).__name__ if r_attr is not None else None,
    )

print("---- force import remaining router modules with tracebacks ----")
fails = []
for m in pkgutil.walk_packages(routers.__path__, "routers."):
    if m.name in sys.modules:
        continue
    try:
        importlib.import_module(m.name)
        print("LATE_OK", m.name)
    except Exception as exc:  # noqa: BLE001
        fails.append(m.name)
        print("LATE_FAIL", m.name, "->", repr(exc))
print("LATE_FAILS:", fails)
print("FINAL_TOTAL_ROUTES:", len(main.app.routes))
print("FINAL_API_ROUTES:", len([r for r in main.app.routes if str(getattr(r, 'path', '')).startswith('/api')]))

try:
    from models.Events import Events

    print("Events PK columns:", [c.name for c in Events.__table__.primary_key.columns])
except Exception as exc:  # noqa: BLE001
    print("Events model import FAILED:", repr(exc))
