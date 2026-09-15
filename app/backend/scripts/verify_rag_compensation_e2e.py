"""RAG 同步补偿闭环端到端验证（真实 PostgreSQL + 真实 HTTP 桩 RAG 服务）。

验证矩阵（与 rag_sync.py 补偿语义一一对应）：
  A. upsert 成功闭环：同步 + 回读验证 + 缓存失效，无补偿任务残留；
  B. 同步失败 → 幂等入队（重复触发不重复入队）→ 补偿重试成功清账；
  C. 持续失败 → 指数退避重试 → 超限进入死信（记录 HTTP 状态与错误）；
  D. 死信人工重放：重置后立即执行成功，审计留痕；
  E. 反馈补偿 + 幂等键防重复加分：重复投递同一键不二次计分；
  F. delete 补偿：归档删除失败入队，重试后索引删除并验证不可见；
  G. 缓存失效补偿：invalidate 失败入队，重试补发广播；
  H. rag_base_url 未配置：业务降级跳过且不入队，审计留痕；
  I. 版本守卫：旧版本覆盖 409 终态拒绝（不入队），遗留补偿任务遇 409 清账不进死信。

运行方式（backend 根目录）：python scripts/verify_rag_compensation_e2e.py
"""
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import delete, select

from core.database import db_manager
from models.audit_logs import Audit_logs
from models.console_configs import Console_configs
from models.rag_sync_tasks import Rag_sync_tasks
import services.rag_sync as rag_sync

E2E_PREFIX = "E2E-CASE-"
STUB_HOST, STUB_PORT = "127.0.0.1", 18099
STUB_BASE = f"http://{STUB_HOST}:{STUB_PORT}"

# ---------------- 桩 RAG 服务（模拟真实 RAG 端点行为与故障注入） ----------------
stub_app = FastAPI()
stub_state = {
    "fail_upsert_case_ids": set(),      # 持续失败的 case_id（场景 C）
    "fail_upsert_first": 0,             # 前 N 次 upsert 失败（场景 B）
    "upsert_attempts": 0,
    "fail_feedback_remaining": 0,
    "fail_delete": False,
    "fail_cache": False,
    "verify_returns_row": False,        # /verify 返回 exists 的当前值
    "guard_enabled": False,             # 版本守卫开关（场景 I）
    "index_versions": {},               # 守卫开启时记录的索引侧 kb_version
    "seen_feedback_keys": set(),
    "dedup_hits": 0,
    "feedback_score": 0,
    "upserted": [],
    "deleted": [],
    "cache_invalidations": [],
}


@stub_app.post("/api/v1/kb-sync/upsert")
async def stub_upsert(request: Request):
    body = await request.json()
    stub_state["upsert_attempts"] += 1
    if body.get("case_id") in stub_state["fail_upsert_case_ids"] or stub_state["fail_upsert_first"] > 0:
        if stub_state["fail_upsert_first"] > 0:
            stub_state["fail_upsert_first"] -= 1
        return JSONResponse(status_code=503, content={"detail": "stub unavailable"})
    case_id = body.get("case_id")
    if stub_state["guard_enabled"]:
        # 模拟真实 RAG 版本守卫：旧版本覆盖 → 409 stale_version_rejected（FastAPI detail 包裹形状）
        idx_ver = int(stub_state["index_versions"].get(case_id) or 0)
        req_ver = body.get("kb_version")
        if req_ver is not None and idx_ver > int(req_ver):
            return JSONResponse(status_code=409, content={"detail": {
                "error": "stale_version_rejected", "case_id": case_id,
                "index_version": idx_ver, "request_version": int(req_ver),
            }})
    stub_state["upserted"].append(case_id)
    stub_state["verify_returns_row"] = True
    if stub_state["guard_enabled"]:
        stub_state["index_versions"][case_id] = int(body.get("kb_version") or 0)
    return {"status": "success", "case_id": case_id, "exists": False, "idempotent": False}


@stub_app.post("/api/v1/kb-sync/delete")
async def stub_delete(request: Request):
    body = await request.json()
    if stub_state["fail_delete"]:
        return JSONResponse(status_code=503, content={"detail": "stub unavailable"})
    stub_state["deleted"].extend(body.get("case_ids", []))
    stub_state["verify_returns_row"] = False  # 删除后回读应不可见
    return {"status": "success", "deleted": body.get("case_ids", [])}


@stub_app.get("/api/v1/kb-sync/verify")
async def stub_verify(case_id: str = ""):
    return {"case_id": case_id, "exists": stub_state["verify_returns_row"]}


@stub_app.post("/api/v1/feedback")
async def stub_feedback(request: Request):
    body = await request.json()
    if stub_state["fail_feedback_remaining"] > 0:
        stub_state["fail_feedback_remaining"] -= 1
        return JSONResponse(status_code=503, content={"detail": "stub unavailable"})
    key = body.get("idempotency_key") or ""
    if key and key in stub_state["seen_feedback_keys"]:
        stub_state["dedup_hits"] += 1  # 幂等键重复投递：不加分
        return {"status": "success", "case_id": body.get("case_id"), "feedback_score": stub_state["feedback_score"]}
    if key:
        stub_state["seen_feedback_keys"].add(key)
    stub_state["feedback_score"] += int(body.get("score") or 0)
    return {"status": "success", "case_id": body.get("case_id"), "feedback_score": stub_state["feedback_score"]}


@stub_app.post("/api/v1/kb-cache/invalidate")
async def stub_cache_invalidate(request: Request):
    body = await request.json()
    if stub_state["fail_cache"]:
        return JSONResponse(status_code=503, content={"detail": "stub unavailable"})
    stub_state["cache_invalidations"].append(body)
    return {"status": "success", "case_id": body.get("case_id"), "cleared_entries": 1, "broadcast": True}


# ---------------- 断言与工具 ----------------
RESULTS = []


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(cond), detail))
    mark = "PASS" if cond else "FAIL"
    print(f"  [{mark}] {name}" + (f" | {detail}" if detail and not cond else ""))


def make_case(case_id: str, version: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        case_id=case_id, service_name="order-service", cluster="prod",
        error_type="redis_timeout", root_cause="连接池耗尽", solution="扩容连接池",
        alert_template="jedis timeout", topology_snapshot="", fingerprint=f"fp-{case_id}",
        feedback_score=2, version=version,
    )


async def fetch_task(db, idem_prefix: str):
    rows = (await db.execute(
        select(Rag_sync_tasks).where(Rag_sync_tasks.idempotency_key.like(f"{idem_prefix}%"))
    )).scalars().all()
    return rows


async def pending_count(db, case_id: str) -> int:
    rows = (await db.execute(
        select(Rag_sync_tasks).where(
            Rag_sync_tasks.case_id == case_id, Rag_sync_tasks.status == "pending"
        )
    )).scalars().all()
    return len(rows)


async def set_config(db, key: str, value: str) -> None:
    row = (await db.execute(
        select(Console_configs).where(Console_configs.config_key == key)
    )).scalar_one_or_none()
    if row is None:
        db.add(Console_configs(config_key=key, config_value=value, description="e2e"))
    else:
        row.config_value = value
    await db.commit()


async def get_config_row(db, key: str):
    return (await db.execute(
        select(Console_configs).where(Console_configs.config_key == key)
    )).scalar_one_or_none()


# ---------------- 场景 ----------------
async def scenario_a(db):
    print("\n[A] upsert 成功闭环（同步 + 验证 + 缓存失效）")
    res = await rag_sync.sync_case_upsert(db, "e2e-admin", make_case(f"{E2E_PREFIX}A1"))
    check("A1 同步成功且回读验证通过", res.get("ok") is True and res.get("verified") is True, str(res))
    check("A2 桩服务收到 upsert", stub_state["upserted"] == [f"{E2E_PREFIX}A1"])
    check("A3 触发缓存失效广播", any(c.get("case_id") == f"{E2E_PREFIX}A1" and c.get("reason") == "upsert"
                                    for c in stub_state["cache_invalidations"]))
    check("A4 无补偿任务残留", (await fetch_task(db, f"upsert:{E2E_PREFIX}A1")) == [])


async def scenario_b(db):
    print("\n[B] 同步失败 → 幂等入队 → 补偿重试成功")
    stub_state["fail_upsert_case_ids"].add(f"{E2E_PREFIX}B2")
    case = make_case(f"{E2E_PREFIX}B2", version=2)
    res1 = await rag_sync.sync_case_upsert(db, "e2e-admin", case)
    res2 = await rag_sync.sync_case_upsert(db, "e2e-admin", case)  # 重复触发
    check("B1 两次触发均失败并降级", res1.get("ok") is False and res2.get("ok") is False)
    upsert_tasks = await fetch_task(db, f"upsert:{E2E_PREFIX}B2:")
    cache_tasks = await fetch_task(db, f"cache_invalidate:{E2E_PREFIX}B2:")
    check("B2 幂等入队：upsert 任务仅 1 条", len(upsert_tasks) == 1, f"got {len(upsert_tasks)}")
    check("B3 幂等入队：cache 任务仅 1 条", len(cache_tasks) == 1, f"got {len(cache_tasks)}")
    stub_state["fail_upsert_case_ids"].clear()
    summary = await rag_sync.retry_due_tasks(db, limit=10)
    check("B4 到期重试执行 2 条全部成功",
          summary["executed"] == 2 and summary["succeeded"] == 2 and summary["dead_lettered"] == 0, str(summary))
    upsert_tasks = await fetch_task(db, f"upsert:{E2E_PREFIX}B2:")
    check("B5 任务清账：done + verified", all(t.status == "done" and t.verified for t in upsert_tasks))


async def scenario_c(db):
    print("\n[C] 持续失败 → 指数退避 → 死信")
    stub_state["fail_upsert_case_ids"].add(f"{E2E_PREFIX}C3")
    # 加速验证：退避基数 1 秒、任务级最大尝试 2 次（ monkeypatch 不影响生产配置 ）
    orig_base, orig_max = rag_sync._retry_base_seconds, rag_sync._max_attempts_default

    async def fast_base(_db):
        return 1

    async def two_attempts(_db):
        return 2

    rag_sync._retry_base_seconds = fast_base
    rag_sync._max_attempts_default = two_attempts
    try:
        await rag_sync.sync_case_upsert(db, "e2e-admin", make_case(f"{E2E_PREFIX}C3", version=3))
        tasks = await fetch_task(db, f"upsert:{E2E_PREFIX}C3:")
        check("C1 失败入队且任务级 max_attempts=2", len(tasks) == 1 and tasks[0].max_attempts == 2)

        await rag_sync.retry_due_tasks(db, limit=10)          # 第 1 次尝试失败 → 退避 1s
        await asyncio.sleep(1.2)
        await rag_sync.retry_due_tasks(db, limit=10)          # 第 2 次尝试失败 → 死信
        tasks = await fetch_task(db, f"upsert:{E2E_PREFIX}C3:")
        t = tasks[0]
        check("C2 达到上限进入死信", t.status == "dead" and t.attempt_count == 2, f"{t.status}/{t.attempt_count}")
        check("C3 记录 HTTP 503 与错误详情", t.last_http_status == 503 and "HTTPStatusError" in (t.last_error or ""))
        check("C4 死信不再排期", t.next_retry_at is None)

        summary = await rag_sync.retry_due_tasks(db, limit=10)
        check("C5 死信不被自动重试扫描", summary["executed"] == 0, str(summary))
    finally:
        rag_sync._retry_base_seconds, rag_sync._max_attempts_default = orig_base, orig_max
        stub_state["fail_upsert_case_ids"].clear()
    return tasks[0].id


async def scenario_d(db, dead_task_id):
    print("\n[D] 死信人工重放")
    stub_state["verify_returns_row"] = False
    res = await rag_sync.replay_task(db, "e2e-admin", dead_task_id)
    check("D1 重放成功清账", res.get("ok") is True and res.get("status") == "done", str(res))
    tasks = (await db.execute(
        select(Rag_sync_tasks).where(Rag_sync_tasks.id == dead_task_id)
    )).scalars().all()
    check("D2 任务状态 done + verified", bool(tasks) and tasks[0].status == "done" and tasks[0].verified)
    audits = (await db.execute(
        select(Audit_logs).where(Audit_logs.action == "rag_sync_task_replay", Audit_logs.target_id == str(dead_task_id))
    )).scalars().all()
    check("D3 重放审计留痕", len(audits) >= 1)


async def scenario_e(db):
    print("\n[E] 反馈补偿 + 幂等键防重复加分")
    stub_state["fail_feedback_remaining"] = 1
    stub_state["feedback_score"] = 0
    res = await rag_sync.sync_feedback(db, "e2e-admin", f"{E2E_PREFIX}E5", +1)
    check("E1 首次同步失败入补偿队列", res.get("ok") is False)
    fb_tasks = await fetch_task(db, "feedback:E2E-CASE-E5:")
    check("E2 反馈任务带独立幂等键", len(fb_tasks) == 1 and fb_tasks[0].payload_json.count("idempotency_key") == 1)

    stub_state["fail_feedback_remaining"] = 0
    await rag_sync.retry_due_tasks(db, limit=10)
    check("E3 补偿成功且仅加 1 分", stub_state["feedback_score"] == 1, f"score={stub_state['feedback_score']}")

    # 模拟重复投递：把已 done 的任务重置为 pending，再次补偿执行
    fb_tasks[0].status = "pending"
    fb_tasks[0].next_retry_at = None
    await db.commit()
    await rag_sync.retry_due_tasks(db, limit=10)
    check("E4 同一幂等键重复投递不二次计分",
          stub_state["feedback_score"] == 1 and stub_state["dedup_hits"] == 1,
          f"score={stub_state['feedback_score']} dedup={stub_state['dedup_hits']}")


async def scenario_f(db):
    print("\n[F] delete 补偿（归档删除失败 → 重试 → 索引删除验证）")
    stub_state["verify_returns_row"] = True  # 先让索引"可见"
    stub_state["fail_delete"] = True
    res = await rag_sync.sync_cases_delete(db, "e2e-admin", [f"{E2E_PREFIX}F6"])
    check("F1 删除失败降级入队", res.get("ok") is False)
    del_tasks = await fetch_task(db, f"delete:{E2E_PREFIX}F6:")
    check("F2 delete 任务入队", len(del_tasks) == 1)
    stub_state["fail_delete"] = False
    stub_state["verify_returns_row"] = True
    await rag_sync.retry_due_tasks(db, limit=10)
    del_tasks = await fetch_task(db, f"delete:{E2E_PREFIX}F6:")
    check("F3 重试后删除成功且验证不可见", all(t.status == "done" and t.verified for t in del_tasks)
          and f"{E2E_PREFIX}F6" in stub_state["deleted"])


async def scenario_g(db):
    print("\n[G] 缓存失效补偿（invalidate 失败 → 重试补发）")
    stub_state["fail_cache"] = True
    res = await rag_sync.sync_case_upsert(db, "e2e-admin", make_case(f"{E2E_PREFIX}G7", version=4))
    check("G1 upsert 成功但缓存失效失败入队", res.get("ok") is True
          and len(await fetch_task(db, f"cache_invalidate:{E2E_PREFIX}G7:")) == 1)
    stub_state["fail_cache"] = False
    await rag_sync.retry_due_tasks(db, limit=10)
    check("G2 补偿补发缓存失效广播", any(c.get("case_id") == f"{E2E_PREFIX}G7" for c in stub_state["cache_invalidations"]))
    cache_tasks = await fetch_task(db, f"cache_invalidate:{E2E_PREFIX}G7:")
    check("G3 缓存任务清账", all(t.status == "done" for t in cache_tasks))


async def scenario_i(db):
    print("\n[I] 版本守卫：旧版本覆盖终态拒绝（不进补偿队列 / 遗留任务清账）")
    stub_state["guard_enabled"] = True
    try:
        ok_case = make_case(f"{E2E_PREFIX}I1", version=5)
        ok_case.root_cause = "v5 根因：连接池耗尽（已扩容）"
        res = await rag_sync.sync_case_upsert(db, "e2e-admin", ok_case)
        check("I1 v5 首次同步成功", res.get("ok") is True, str(res))
        check("I2 桩索引版本记录为 5", stub_state["index_versions"].get(f"{E2E_PREFIX}I1") == 5)

        stale_case = make_case(f"{E2E_PREFIX}I1", version=4)
        stale_case.root_cause = "v4 旧根因：重启解决"
        res2 = await rag_sync.sync_case_upsert(db, "e2e-admin", stale_case)
        check("I3 旧版本被 409 终态拒绝（stale_version=True）",
              res2.get("ok") is False and res2.get("stale_version") is True, str(res2))
        upsert_tasks = await fetch_task(db, f"upsert:{E2E_PREFIX}I1:")
        cache_tasks = await fetch_task(db, f"cache_invalidate:{E2E_PREFIX}I1:")
        check("I4 被拒后不产生任何补偿任务", list(upsert_tasks) == [] and list(cache_tasks) == [])
        check("I5 索引版本未回退（保持 5）", stub_state["index_versions"].get(f"{E2E_PREFIX}I1") == 5)

        # 遗留补偿任务（守卫上线前入队的旧版本 upsert）遇 409：终态清账 done，不进死信
        legacy_payload = {
            "case_id": f"{E2E_PREFIX}I1", "kb_version": 4,
            "root_cause": "v4 旧根因：重启解决", "solution": "扩容连接池",
            "alert_template": "jedis timeout", "content_hash": "legacy",
        }
        await rag_sync._enqueue_task(db, "upsert", f"{E2E_PREFIX}I1", legacy_payload,
                                     "legacy-stale-hash", created_by="e2e-admin")
        summary = await rag_sync.retry_due_tasks(db, limit=10)
        legacy_tasks = await fetch_task(db, f"upsert:{E2E_PREFIX}I1:legacy")
        t = legacy_tasks[0]
        check("I6 遗留旧版本任务终态清账为 done", t.status == "done" and t.verified is False,
              f"{t.status}/verified={t.verified}")
        check("I7 清账详情记录 resolved=stale_version", "stale_version" in (t.verify_detail or ""))
        check("I8 不进入死信且不再重试", summary["dead_lettered"] == 0 and t.next_retry_at is None, str(summary))
    finally:
        stub_state["guard_enabled"] = False


async def scenario_h(db):
    print("\n[H] rag_base_url 未配置 → 业务降级跳过")
    before_pending = await pending_count(db, f"{E2E_PREFIX}H8")
    res = await rag_sync.sync_case_upsert(db, "e2e-admin", make_case(f"{E2E_PREFIX}H8"))
    check("H1 同步跳过且不入队", res.get("ok") is False and "skipped" in res
          and (await pending_count(db, f"{E2E_PREFIX}H8")) == before_pending, str(res))
    audits = (await db.execute(
        select(Audit_logs).where(
            Audit_logs.action == "rag_index_sync",
            Audit_logs.target_type == "kb_case",
            Audit_logs.target_id == f"{E2E_PREFIX}H8",
        )
    )).scalars().all()
    check("H2 降级审计留痕", len(audits) >= 1)


# ---------------- 生命周期 ----------------
async def cleanup(db):
    """清理本脚本产生的补偿任务与审计记录（幂等，可重复运行）。"""
    stub_state["guard_enabled"] = False
    stub_state["index_versions"].clear()
    await db.execute(delete(Rag_sync_tasks).where(Rag_sync_tasks.case_id.like(f"{E2E_PREFIX}%")))
    await db.execute(delete(Audit_logs).where(
        (Audit_logs.target_type == "kb_case") & (Audit_logs.target_id.like(f"{E2E_PREFIX}%"))
    ))
    await db.execute(delete(Audit_logs).where(
        (Audit_logs.target_type == "rag_sync_task") & (Audit_logs.action.like("rag_sync_task_replay%"))
    ))
    await db.commit()


async def main() -> int:
    await db_manager.ensure_connected()
    server = uvicorn.Config(stub_app, host=STUB_HOST, port=STUB_PORT, log_level="warning")
    uv_server = uvicorn.Server(server)
    serve_task = asyncio.create_task(uv_server.serve())
    while not uv_server.started:
        await asyncio.sleep(0.05)
    print(f"stub RAG server started at {STUB_BASE}")

    try:
        async with db_manager.session() as db:
            await cleanup(db)
            url_row = await get_config_row(db, "rag_base_url")
            original_url = url_row.config_value if url_row else None
            await set_config(db, "rag_base_url", STUB_BASE)
            try:
                await scenario_a(db)
                await scenario_b(db)
                dead_id = await scenario_c(db)
                await scenario_d(db, dead_id)
                await scenario_e(db)
                await scenario_f(db)
                await scenario_g(db)
                await scenario_i(db)
                # 场景 H：临时置空 RAG 地址
                await set_config(db, "rag_base_url", "")
                await scenario_h(db)
            finally:
                await set_config(db, "rag_base_url", original_url or "")
                if original_url is None:
                    row = await get_config_row(db, "rag_base_url")
                    if row is not None and row.description == "e2e":
                        await db.delete(row)
                        await db.commit()
                await cleanup(db)
    finally:
        uv_server.should_exit = True
        await serve_task

    failed = [name for name, ok, _ in RESULTS if not ok]
    print(f"\n===== E2E RESULT: {len(RESULTS) - len(failed)}/{len(RESULTS)} passed =====")
    if failed:
        for name in failed:
            print(f"  FAILED: {name}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
