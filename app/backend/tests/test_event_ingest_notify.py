"""事件同步入口与诊断后通知推送专项测试。

覆盖：
- POST /api/v1/ingest/alerts 单条同步：RAG webhook 契约载荷 → events 表 status=pending；
- 幂等去重：同 event_id 重复推送返回 duplicated，不重复入库；
- X-Ingest-Token 鉴权：未配置放行（fail-open）、配置后缺失/错误 401、正确放行；
- severity 归一化（整数 1/2/3 → info/warning/critical）与关键词兜底分类；
- 配置校验与密钥加密往返（notify_webhook_token / event_ingest_token）；
- 通知载荷构造（根因/建议/置信度/Trust Index）与 httpx 推送成功/失败不抛；
- notify_after_diagnosis 调度语义（未配置跳过、配置后调度推送）。
"""

import asyncio
import json
import time

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.database import Base, get_db
from models import Events as events_module  # noqa: F401  注册表
from models import audit_logs  # noqa: F401  注册表
from models import console_configs  # noqa: F401  注册表
from models.Events import Events
from routers.ingest import BATCH_LIMIT, router as ingest_router
from services import notify_service
from services.console_common import validate_config_value
from services.console_common import set_config
from services.llm_runtime import SECRET_CONFIG_KEYS, decrypt_secret, encrypt_secret


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def ingest_client(db_session):
    """挂载 ingest 路由的测试客户端（get_db 覆盖为测试会话）。"""
    app = FastAPI()
    app.include_router(ingest_router)

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def _webhook_payload(**overrides):
    """RAG webhook 契约样例载荷。"""
    body = {
        "source": "umps",
        "raw_message": "orders pod OOMKilled in cluster c1",
        "labels": {"service": "orders", "cluster": "c1", "namespace": "prod"},
        "timestamp": int(time.time() * 1000),
        "event_id": "EV-TEST-1",
        "error_type": "oom_killed",
        "template": "OOMKilled",
        "severity": 3,
    }
    body.update(overrides)
    return body


async def _count_events(db_session: AsyncSession) -> int:
    result = await db_session.execute(select(Events))
    return len(result.scalars().all())


@pytest.mark.asyncio
async def test_ingest_accepts_and_persists_pending_event(ingest_client, db_session):
    """单条同步：accepted=1，事件落库为 pending，severity 整数归一化为 critical。"""
    resp = await ingest_client.post("/api/v1/ingest/alerts", json=_webhook_payload())
    assert resp.status_code == 200
    data = resp.json()
    assert data["accepted"] == 1
    assert data["duplicated"] == 0
    assert data["event_id"] == "EV-TEST-1"

    row = (await db_session.execute(select(Events).where(Events.event_id == "EV-TEST-1"))).scalar_one()
    assert row.status == "pending"
    assert row.service_name == "orders"  # labels.service 提取
    assert row.cluster == "c1"
    assert row.severity == "critical"
    assert row.error_type == "oom_killed"
    assert row.template == "OOMKilled"
    assert row.fingerprint  # 指纹已生成
    assert row.raw_log.startswith("orders pod")


@pytest.mark.asyncio
async def test_ingest_idempotent_dedup_by_event_id(ingest_client, db_session):
    """幂等去重：同 event_id 二次推送返回 duplicated，表中仍只有一条。"""
    first = await ingest_client.post("/api/v1/ingest/alerts", json=_webhook_payload())
    assert first.json()["accepted"] == 1
    second = await ingest_client.post("/api/v1/ingest/alerts", json=_webhook_payload())
    assert second.status_code == 200
    assert second.json()["duplicated"] == 1
    assert await _count_events(db_session) == 1


@pytest.mark.asyncio
async def test_ingest_batch_results_and_limits(ingest_client, db_session):
    """批量同步：去重计数正确；超上限 400；空数组 400。"""
    payload = {
        "events": [
            _webhook_payload(event_id="EV-B1"),
            _webhook_payload(event_id="EV-B2", severity=1, error_type="disk_full"),
            _webhook_payload(event_id="EV-B1"),  # 批内重复
        ]
    }
    resp = await ingest_client.post("/api/v1/ingest/alerts/batch", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 3
    assert data["accepted"] == 2
    assert data["duplicated"] == 1
    info_row = (await db_session.execute(select(Events).where(Events.event_id == "EV-B2"))).scalar_one()
    assert info_row.severity == "info"

    empty = await ingest_client.post("/api/v1/ingest/alerts/batch", json={"events": []})
    assert empty.status_code == 400

    oversized = {"events": [_webhook_payload(event_id=f"EV-X{i}") for i in range(BATCH_LIMIT + 1)]}
    too_many = await ingest_client.post("/api/v1/ingest/alerts/batch", json=oversized)
    assert too_many.status_code == 400


@pytest.mark.asyncio
async def test_ingest_token_auth_matrix(ingest_client, db_session):
    """Token 鉴权矩阵：未配置放行；配置后缺失/错误 401；正确放行。"""
    # 未配置：fail-open 放行
    ok = await ingest_client.post("/api/v1/ingest/alerts", json=_webhook_payload(event_id="EV-OPEN"))
    assert ok.status_code == 200

    # 配置后：缺失 / 错误 → 401
    await set_config(db_session, "event_ingest_token", "tok-secret-123")
    missing = await ingest_client.post("/api/v1/ingest/alerts", json=_webhook_payload(event_id="EV-MISS"))
    assert missing.status_code == 401
    wrong = await ingest_client.post(
        "/api/v1/ingest/alerts",
        json=_webhook_payload(event_id="EV-WRONG"),
        headers={"X-Ingest-Token": "bad-token"},
    )
    assert wrong.status_code == 401
    assert await _count_events(db_session) == 1  # 被拒请求未入库

    # 正确 Token → 放行
    right = await ingest_client.post(
        "/api/v1/ingest/alerts",
        json=_webhook_payload(event_id="EV-RIGHT"),
        headers={"X-Ingest-Token": "tok-secret-123"},
    )
    assert right.status_code == 200
    assert right.json()["accepted"] == 1


@pytest.mark.asyncio
async def test_severity_mapping_and_keyword_classification(ingest_client, db_session):
    """severity 归一化 + 无 error_type 时关键词兜底分类（OOMKilled → critical）。"""
    resp = await ingest_client.post(
        "/api/v1/ingest/alerts",
        json=_webhook_payload(event_id="EV-CLS", severity=None, error_type=None),
    )
    assert resp.status_code == 200
    row = (await db_session.execute(select(Events).where(Events.event_id == "EV-CLS"))).scalar_one()
    assert row.error_type == "oom_killed"  # raw_message 含 OOMKilled 命中关键词规则
    assert row.severity == "critical"  # 分类规则默认级别

    # 完全无法分类：error_type/template 为空、severity 缺省 warning
    resp2 = await ingest_client.post(
        "/api/v1/ingest/alerts",
        json=_webhook_payload(event_id="EV-CLS2", raw_message="something odd happened", template=None, severity=None, error_type=None),
    )
    assert resp2.status_code == 200
    row2 = (await db_session.execute(select(Events).where(Events.event_id == "EV-CLS2"))).scalar_one()
    assert row2.error_type is None
    assert row2.severity == "warning"


def test_secret_config_keys_and_encrypt_roundtrip():
    """两个 token 键纳入密钥清单；加密→解密往返保真。"""
    assert "notify_webhook_token" in SECRET_CONFIG_KEYS
    assert "event_ingest_token" in SECRET_CONFIG_KEYS
    cipher = encrypt_secret("plain-token")
    assert cipher.startswith("enc:")
    assert decrypt_secret(cipher) == "plain-token"
    assert decrypt_secret("") == ""
    assert decrypt_secret("legacy-plain") == "legacy-plain"  # 历史明文兼容


def test_config_validation_rules():
    """配置写入校验：通知地址须 http(s) 开头；Token 拒绝脱敏占位符。"""
    assert validate_config_value("notify_webhook_url", "")[0] is True
    assert validate_config_value("notify_webhook_url", "https://itsm.example.com/webhook")[0] is True
    assert validate_config_value("notify_webhook_url", "ftp://itsm.example.com")[0] is False
    assert validate_config_value("notify_webhook_token", "raw-token")[0] is True
    assert validate_config_value("notify_webhook_token", "sk-a****wxyz")[0] is False
    assert validate_config_value("event_ingest_token", "tok-123")[0] is True
    assert validate_config_value("event_ingest_token", "ab****cd")[0] is False


def _make_event(**overrides) -> Events:
    kwargs = dict(
        event_id="EV-N1",
        service_name="orders",
        cluster="c1",
        severity="critical",
        status="diagnosed",
        template="OOMKilled",
        error_type="oom_killed",
        raw_log="orders pod OOMKilled",
        ai_root_cause="容器内存超限被 OOMKill",
        ai_solution="调大内存限制至 2Gi 并检查泄漏",
        ai_command="kubectl set resources deploy/orders --limits=memory=2Gi",
        confidence=0.86,
        ai_output_json=json.dumps(
            {"root_cause": "内存超限", "solution": "扩容", "confidence": 0.86, "model": "deepseek-v4-flash",
             "quality": {"trust_index": 0.91, "quality_ok": True}},
            ensure_ascii=False,
        ),
    )
    kwargs.update(overrides)
    return Events(**kwargs)


def test_build_notification_payload_fields():
    """通知载荷字段齐全：根因/建议/命令/置信度/Trust Index/链路元信息。"""
    event = _make_event()
    payload = notify_service.build_notification_payload(
        event, extra={"diagnosis_source": "single_round", "model": "deepseek-v4-flash", "diagnosed_at": "2026-09-18T00:00:00+00:00"}
    )
    assert payload["source"] == "rootsleuth"
    assert payload["event_type"] == "diagnosis_completed"
    assert payload["event_id"] == "EV-N1"
    assert payload["service_name"] == "orders"
    assert payload["severity"] == "critical"
    assert payload["status"] == "diagnosed"
    assert payload["root_cause"] == "容器内存超限被 OOMKill"
    assert payload["solution"] == "调大内存限制至 2Gi 并检查泄漏"
    assert payload["command"].startswith("kubectl")
    assert payload["confidence"] == 0.86
    assert payload["trust_index"] == 0.91  # 从 ai_output_json.quality 提取
    assert payload["diagnosis_source"] == "single_round"
    assert payload["diagnosed_at"] == "2026-09-18T00:00:00+00:00"
    # 无 quality 块时不抛错、trust_index 为 None
    event2 = _make_event(ai_output_json=None)
    payload2 = notify_service.build_notification_payload(event2)
    assert payload2["trust_index"] is None


@pytest.mark.asyncio
async def test_send_notification_success_and_failure():
    """httpx 推送：成功返回 ok；非 2xx 与网络异常转为结构化失败且不抛。"""
    payload = {"event_id": "EV-N1"}

    async def ok_handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("Authorization") == "Bearer tok-1"
        return httpx.Response(200, json={"ok": True})

    summary = await notify_service.send_notification(
        "https://itsm.example.com/hook", "tok-1", payload,
        client=httpx.AsyncClient(transport=httpx.MockTransport(ok_handler)),
    )
    assert summary["ok"] is True
    assert summary["status_code"] == 200

    async def bad_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    summary_bad = await notify_service.send_notification(
        "https://itsm.example.com/hook", "tok-1", payload,
        client=httpx.AsyncClient(transport=httpx.MockTransport(bad_handler)),
    )
    assert summary_bad["ok"] is False
    assert summary_bad["status_code"] == 500
    assert "非 2xx" in summary_bad["error"]

    summary_err = await notify_service.send_notification(
        "https://unreachable.invalid/hook", "", payload,
        client=httpx.AsyncClient(transport=httpx.MockTransport(_raise_transport)),
    )
    assert summary_err["ok"] is False
    assert summary_err["status_code"] is None
    assert summary_err["error"]


async def _raise_transport(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connection refused")


@pytest.mark.asyncio
async def test_notify_after_diagnosis_dispatch(db_session, monkeypatch):
    """诊断后通知调度：未配置 URL 返回 None；配置后调度推送并携带解密 Token。"""
    event = _make_event()
    db_session.add(event)
    await db_session.commit()

    # 未配置：静默跳过
    assert await notify_service.notify_after_diagnosis(db_session, event, extra={}) is None

    scheduled: dict = {}

    def fake_schedule(url: str, token: str, payload: dict):
        scheduled.update({"url": url, "token": token, "payload": payload})
        return asyncio.get_running_loop().create_task(asyncio.sleep(0))

    monkeypatch.setattr(notify_service, "schedule_notification", fake_schedule)
    await set_config(db_session, "notify_webhook_url", "https://itsm.example.com/hook")
    await set_config(db_session, "notify_webhook_token", encrypt_secret("notify-tok"))
    result = await notify_service.notify_after_diagnosis(
        db_session, event, extra={"diagnosis_source": "agent", "model": "m1", "diagnosed_at": "2026-09-18T01:00:00+00:00"}
    )
    assert result == {"url": "https://itsm.example.com/hook", "scheduled": True}
    assert scheduled["url"] == "https://itsm.example.com/hook"
    assert scheduled["token"] == "notify-tok"  # 加密配置解密后随 Bearer 头携带
    assert scheduled["payload"]["root_cause"] == "容器内存超限被 OOMKill"
    assert scheduled["payload"]["diagnosis_source"] == "agent"
