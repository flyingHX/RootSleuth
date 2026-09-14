"""临时诊断脚本 2：逐条打印 app 路由与各 router 内部路由（诊断后删除）。"""
import sys

import main

print("routers pkg file:", sys.modules["routers"].__file__)
print("routers pkg path:", list(sys.modules["routers"].__path__))
print("---- app.routes dump ----")
for i, r in enumerate(main.app.routes):
    print(i, type(r).__name__, "| path=", getattr(r, "path", None), "| name=", getattr(r, "name", None))

print("---- per-module router internals ----")
for mname in [
    "routers.console",
    "routers.auth",
    "routers.aihub",
    "routers.kb_cases",
    "routers.Events",
    "routers.events" if "routers.events" in sys.modules else "routers.kb_versions",
]:
    mod = sys.modules.get(mname)
    if mod is None:
        print(mname, "NOT IMPORTED")
        continue
    rt = getattr(mod, "router", None)
    print(mname, "| router type:", type(rt).__name__, "| prefix:", getattr(rt, "prefix", None), "| n_routes:", len(rt.routes) if rt is not None else None)
    for rr in (rt.routes if rt is not None else [])[:3]:
        print("    ", type(rr).__name__, getattr(rr, "path", None))

print("---- manual include test ----")
from fastapi import FastAPI

test_app = FastAPI()
try:
    test_app.include_router(sys.modules["routers.console"].router)
    print("manual include OK, test routes:", len(test_app.routes))
    for r in test_app.routes[:4]:
        print("    ", getattr(r, "path", None))
except Exception as exc:
    print("manual include FAILED:", repr(exc))
