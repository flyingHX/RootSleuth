"""混合 LLM 分级路由与合规审计专项测试（评审采纳项闭环固证）。

以 SQLite 内存库覆盖六组语义：
1. 路由决策矩阵：auto / local_only / remote_only × 敏感 × critical × 生产等级 × 审批编号；
2. 敏感数据不出域红线：敏感事件永不以 route=remote 调用 LLM，本地未配置时
   降级确定性结论且零 LLM 调用（stub 记录 route kwarg 断言）；
3. 入站脱敏管道逐类掩码（证件/手机/卡号/内网 IP/密钥/Bearer/邮箱/长令牌）
   与掩码占位符二次识别；
4. 合规审计哈希链：链式写入、prev_hash 连接、防篡改校验（broken_id 定位）；
5. ingest 集成：data_masking_enabled 默认开启原始不落库、关闭时原文保留；
6. 诊断链路挂载：单轮 run_diagnosis 将 route 透传到 LLM 调用并写入
   llm_invocation 链审计（deterministic_fallback / local / remote 三形态）。
"""

import json

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.database import Base
from models import audit_logs  # noqa: F401  注册表
from models import cmdb_assets  # noqa: F401
from models import console_configs  # noqa: F401
from models import Events as events_module  # noqa: F401  注册表
from models import kb_cases  # noqa: F401
from models.Events import Events
from models.audit_logs import Audit_logs
from models.cmdb_assets import Cmdb_assets
from models.console_configs import Console_configs
from models.kb_cases import Kb_cases
from schemas.aihub import GenTxtResponse
from routers.ingest import IngestAlertBody, _ingest_one
from services import llm_runtime
from services.console_ai import run_diagnosis
from services.console_common import (
    detect_sensitivity,
    mask_alert_text,
    verify_compliance_chain,
    write_audit_chained,
)
from services.llm_routing import build_deterministic_diagnosis, decide_llm_route

CLEAN_LOG = "disk usage high on worker node partition /data"
SENSITIVE_LOG = "disk alert contact 13812345678"
CHAIN_ACTIONS = ("llm_route_decision", "llm_invocation")


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


async def _set_config(session: AsyncSession, key: str, value: str) -> None:
    """覆盖式写入单个配置项（先删后插，避免多行干扰 get_config 首行语义）。"""
    result = await session.execute(select(Console_configs).where(Console_configs.config_key == key))
    for row in result.scalars().all():
        await session.delete(row)
    session.add(Console_configs(config_key=key, config_value=value, description="test"))
    await session.commit()


async def _chain_rows(session: AsyncSession) -> list:
    result = await session.execute(
        select(Audit_logs).where(Audit_logs.action.in_(CHAIN_ACTIONS)).order_by(Audit_logs.id.asc())
    )
    return list(result.scalars().all())


# ---------------- 1. 路由决策矩阵 ----------------


@pytest.mark.asyncio
async def test_route_decision_matrix(db_session):
    """三维决策矩阵：每条用例独立断言 route 与命中理由片段。"""
    db_session.add_all(
        [
            Cmdb_assets(hostname="h1", ip="10.0.0.1", system_name="svc-test", service_name="svc-test", environment="test"),
            Cmdb_assets(hostname="h2", ip="10.0.0.2", system_name="svc-prod", service_name="svc-prod", environment="prod"),
            Cmdb_assets(hostname="h3", ip="10.0.0.3", system_name="svc-prodword", service_name="svc-prodword", environment="production"),
            Cmdb_assets(hostname="h4", ip="10.0.0.4", system_name="svc-staging", service_name="svc-staging", environment="staging"),
        ]
    )
    await db_session.commit()

    # (policy, raw_log, severity, service, approval, expected_route, reason_fragment, expected_tier)
    cases = [
        ("auto", CLEAN_LOG, "warning", "svc-test", "CMP-001", "remote", "远程", "test"),
        ("auto", SENSITIVE_LOG, "warning", "svc-test", "CMP-001", "local", "绝不送远程", "test"),
        ("auto", CLEAN_LOG, "critical", "svc-test", "CMP-001", "local", "critical", "test"),
        ("auto", CLEAN_LOG, "warning", "svc-prod", "CMP-001", "local", "生产环境", "prod"),
        ("auto", CLEAN_LOG, "warning", "svc-prodword", "CMP-001", "local", "生产环境", "prod"),
        ("auto", CLEAN_LOG, "warning", "svc-test", "", "local", "审批", "test"),
        ("local_only", SENSITIVE_LOG, "critical", "svc-prod", "CMP-001", "local", "local_only", "prod"),
        ("remote_only", CLEAN_LOG, "warning", "svc-test", "CMP-001", "remote", "remote_only", "test"),
        ("remote_only", SENSITIVE_LOG, "warning", "svc-test", "CMP-001", "local", "红线优先", "test"),
        ("remote_only", CLEAN_LOG, "warning", "svc-test", "", "local", "红线兜底", "test"),
        ("unknown_policy", CLEAN_LOG, "warning", "svc-test", "CMP-001", "remote", "远程", "test"),
    ]
    for idx, (policy, raw_log, severity, service, approval, expected_route, frag, tier) in enumerate(cases):
        await _set_config(db_session, "llm_routing_policy", policy)
        await _set_config(db_session, "llm_remote_approval_id", approval)
        event = Events(
            event_id=f"EV-MTX-{idx}",
            template="Disk usage high",
            error_type="disk_full",
            service_name=service,
            cluster="c1",
            severity=severity,
            raw_log=raw_log,
            topology="",
            status="pending",
        )
        db_session.add(event)
        await db_session.flush()

        decision = await decide_llm_route(db_session, event)

        assert decision["route"] == expected_route, f"policy={policy} raw={raw_log!r} sev={severity}"
        assert frag in "".join(decision["reasons"]), f"reason fragment {frag!r} missing: {decision['reasons']}"
        assert decision["sensitive"] is (raw_log == SENSITIVE_LOG)
        assert decision["service_tier"] == tier
        assert decision["approval_present"] is bool(approval)
        assert decision["local_configured"] is False

    # 非法策略回退 auto
    assert decision["policy"] == "auto" and cases[-1][0] == "unknown_policy"


@pytest.mark.asyncio
async def test_route_decision_local_configured_and_audit_chain(db_session):
    """本地 LLM 配置齐备时 local_configured=True，决策记录写入合规审计哈希链。"""
    event = Events(
        event_id="EV-MTX-LOCAL",
        template="Disk usage high",
        error_type="disk_full",
        service_name="svc-test",
        cluster="c1",
        severity="warning",
        raw_log=CLEAN_LOG,
        topology="",
        status="pending",
    )
    db_session.add(event)
    await db_session.commit()

    decision = await decide_llm_route(db_session, event)
    assert decision["local_configured"] is False

    await _set_config(db_session, "llm_local_base_url", "http://ollama:11434/v1")
    await _set_config(db_session, "llm_local_model", "qwen2.5:14b")
    decision = await decide_llm_route(db_session, event)
    assert decision["local_configured"] is True

    rows = await _chain_rows(db_session)
    assert len(rows) == 2
    after = json.loads(rows[-1].after_json)
    assert after["route"] == decision["route"]
    assert after["chain"]["hash"] and (after["chain"]["prev_hash"] is not None)


# ---------------- 2. 敏感数据不出域红线 ----------------


class RouteRecordingLLM:
    """脚本化 LLM 桩：记录每次调用的 route / agent kwarg。"""

    def __init__(self, content: str):
        self.content = content
        self.calls = 0
        self.routes = []
        self.agents = []

    async def __call__(self, db, messages, **kwargs) -> GenTxtResponse:
        self.calls += 1
        self.routes.append(kwargs.get("route"))
        self.agents.append(kwargs.get("agent"))
        return GenTxtResponse(content=self.content, model="stub-model", usage=None)


VALID_JSON = json.dumps(
    {"root_cause": "支付通道异常", "solution": "切换备用支付通道并重试", "confidence": 0.9, "command": ""},
    ensure_ascii=False,
)


def _seed_payment_kb(session: AsyncSession) -> None:
    session.add(
        Kb_cases(
            case_id="kb-pay",
            error_type="payment",
            service_name="payments",
            cluster="c1",
            alert_template="Payment failed",
            root_cause="支付通道超时导致扣款失败",
            solution="切换备用通道并补偿订单",
            topology_snapshot="",
            status="active",
            version=1,
            feedback_score=1,
        )
    )


def _sensitive_event(event_id: str) -> Events:
    return Events(
        event_id=event_id,
        template="Payment failed",
        error_type="payment",
        service_name="payments",
        cluster="c1",
        severity="warning",
        raw_log="payment declined card 6222020200112233 phone 13812345678",
        topology="",
        status="pending",
    )


@pytest.mark.asyncio
async def test_sensitive_local_unavailable_deterministic_no_llm_call(db_session, monkeypatch):
    """红线：敏感 + 本地未配置 → 零 LLM 调用，降级确定性结论，绝不送远程。"""
    _seed_payment_kb(db_session)
    event = _sensitive_event("EV-SENS-1")
    db_session.add(event)
    await db_session.commit()

    stub = RouteRecordingLLM(VALID_JSON)
    monkeypatch.setattr(llm_runtime, "llm_chat", stub)

    result = await run_diagnosis(db_session, event.id, "tester")

    assert stub.calls == 0, "敏感数据在本地 LLM 未配置时不得发起任何 LLM 调用"
    assert result["status"] == "success"
    assert result["diagnosis"]["route"] == "deterministic_fallback"
    assert "敏感数据不出域·确定性结论" in result["diagnosis"]["root_cause"]
    assert "kb-pay" in result["diagnosis"]["root_cause"]

    await db_session.refresh(event)
    assert event.status == "diagnosed"
    assert event.degraded_reason == "sensitive_local_unavailable_deterministic"

    # 链审计：路由决策 local + LLM 调用 deterministic_fallback
    rows = await _chain_rows(db_session)
    by_action = {row.action: json.loads(row.after_json) for row in rows}
    assert by_action["llm_route_decision"]["route"] == "local"
    assert by_action["llm_route_decision"]["sensitive"] is True
    inv = by_action["llm_invocation"]
    assert inv["route"] == "deterministic_fallback"
    assert inv["deterministic_reason"] == "sensitive_local_unavailable_deterministic"
    assert inv["sensitive"] is True


@pytest.mark.asyncio
async def test_sensitive_with_local_configured_routes_local(db_session, monkeypatch):
    """红线：敏感数据 + 本地已配置 → LLM 以 route=local 调用，模型取 llm_local_model。"""
    _seed_payment_kb(db_session)
    event = _sensitive_event("EV-SENS-2")
    db_session.add(event)
    await db_session.commit()
    await _set_config(db_session, "llm_local_base_url", "http://ollama:11434/v1")
    await _set_config(db_session, "llm_local_model", "qwen2.5:14b")

    stub = RouteRecordingLLM(VALID_JSON)
    monkeypatch.setattr(llm_runtime, "llm_chat", stub)

    result = await run_diagnosis(db_session, event.id, "tester")

    assert stub.routes == ["local"]
    assert stub.agents == ["diagnose"]
    assert result["diagnosis"]["route"] == "local"
    assert result["diagnosis"]["model"] == "qwen2.5:14b"
    await db_session.refresh(event)
    assert event.status == "diagnosed"
    assert event.degraded_reason is None
    rows = await _chain_rows(db_session)
    inv = json.loads([r for r in rows if r.action == "llm_invocation"][-1].after_json)
    assert inv["route"] == "local" and inv["deterministic_reason"] is None and inv["sensitive"] is True


@pytest.mark.asyncio
async def test_non_sensitive_with_approval_routes_remote(db_session, monkeypatch):
    """非敏感 + 审批编号齐备 → route=remote 透传到 LLM 调用。"""
    _seed_payment_kb(db_session)
    event = Events(
        event_id="EV-REMOTE-1",
        template="Payment failed",
        error_type="payment",
        service_name="payments",
        cluster="c1",
        severity="warning",
        raw_log="payment declined for user segment checkout",
        topology="",
        status="pending",
    )
    db_session.add(event)
    await db_session.commit()
    await _set_config(db_session, "llm_remote_approval_id", "CMP-2026-001")

    stub = RouteRecordingLLM(VALID_JSON)
    monkeypatch.setattr(llm_runtime, "llm_chat", stub)

    result = await run_diagnosis(db_session, event.id, "tester")

    assert stub.routes == ["remote"]
    assert result["diagnosis"]["route"] == "remote"
    rows = await _chain_rows(db_session)
    decision_after = json.loads([r for r in rows if r.action == "llm_route_decision"][-1].after_json)
    assert decision_after["route"] == "remote" and decision_after["sensitive"] is False
    inv = json.loads([r for r in rows if r.action == "llm_invocation"][-1].after_json)
    assert inv["route"] == "remote"


def test_build_deterministic_diagnosis_unit():
    """确定性结论纯函数：前缀/兜底文案/置信度/空候选边界。"""
    out = build_deterministic_diagnosis(
        [{"case_id": "kb-1", "root_cause": "磁盘满", "solution": None, "score": 0.83}]
    )
    assert "敏感数据不出域·确定性结论" in out["root_cause"] and "kb-1" in out["root_cause"]
    assert "磁盘满" in out["root_cause"]
    assert out["solution"].startswith("请人工")
    assert out["confidence"] == 0.83 and out["command"] == ""
    assert build_deterministic_diagnosis([]) is None
    assert build_deterministic_diagnosis([{"case_id": "x", "root_cause": "", "solution": ""}]) is None


# ---------------- 3. 入站脱敏管道逐类掩码 ----------------


def test_mask_alert_text_personal_categories():
    text = "id 11010119900307851X phone 13812345678 card 6222020200112233 ip 10.1.2.3 gw 192.168.1.100"
    masked = mask_alert_text(text)
    assert "11010119900307851X" not in masked and "110****851X" in masked
    assert "13812345678" not in masked and "138****5678" in masked
    assert "6222020200112233" not in masked and "6222****2233" in masked
    assert "10.1.2.3" not in masked and "10.1.2.x" in masked
    assert "192.168.1.100" not in masked and "192.168.1.x" in masked
    # 公网 IP 不掩码
    public = mask_alert_text("upstream 8.8.8.8 timeout")
    assert "8.8.8.8" in public


def test_mask_alert_text_credentials():
    long_token = "a" * 32
    text = f"password=Sup3rSecret! Authorization: Bearer abc.def-ghi mail user@example.com tok {long_token}"
    masked = mask_alert_text(text)
    assert "Sup3rSecret" not in masked and "password=***" in masked
    # Bearer 令牌值不残留（Authorization 头场景先掩码，防止键名被吞后明文残留）
    assert "abc.def-ghi" not in masked and "Bearer abc" not in masked
    assert "user@example.com" not in masked and "***@***" in masked
    assert long_token not in masked


def test_detect_sensitivity_raw_and_masked():
    cases = [
        ("contact 13812345678", ["phone_number"]),
        # 17 位纯数字身份证同时落入 15~19 位长数字类别（两类均命中符合判定语义）
        ("id 11010119900307851X", ["bank_card_or_long_digits", "id_card"]),
        ("card 6222020200112233", ["bank_card_or_long_digits"]),
        ("pod lost contact with 10.1.2.3", ["internal_ip"]),
        ("gateway 10.1.2.x unreachable", ["internal_ip"]),
        ("password=abc", ["credential_assignment"]),
        ("Bearer abc123", ["bearer_token"]),
        ("mail user@example.com", ["email"]),
        ("masked 138****5678", ["masked_placeholder"]),
    ]
    for text, expected in cases:
        sensitive, categories = detect_sensitivity(text)
        assert sensitive is True, text
        assert categories == expected, f"{text}: {categories}"
    assert detect_sensitivity("disk usage high on worker node") == (False, [])


def test_detect_sensitivity_on_masked_output():
    """掩码占位符二次识别：脱敏后的文本仍可判定敏感并驱动 LLM 路由。"""
    sensitive, categories = detect_sensitivity(mask_alert_text("card 6222020200112233 phone 13812345678"))
    assert sensitive is True
    assert "masked_placeholder" in categories


# ---------------- 4. 合规审计哈希链防篡改 ----------------


@pytest.mark.asyncio
async def test_audit_chain_write_and_verify(db_session):
    r1 = await write_audit_chained(
        db_session, actor="llm_routing", action="llm_route_decision", target_type="event", target_id=1,
        after={"route": "local"},
    )
    r2 = await write_audit_chained(
        db_session, actor="tester", action="llm_invocation", target_type="event", target_id=1,
        after={"route": "local", "model": "stub-model"},
    )
    assert r1["prev_hash"] is None and r1["hash"]
    assert r2["prev_hash"] == r1["hash"] and r2["hash"] != r1["hash"]

    result = await verify_compliance_chain(db_session)
    assert result["ok"] is True
    assert result["total"] == 2
    assert result["head_hash"] == r2["hash"]
    assert result["broken_id"] is None


@pytest.mark.asyncio
async def test_audit_chain_tamper_detection(db_session):
    """篡改链上任一记录的业务字段（保留链块）→ 整链校验失败并定位 broken_id。"""
    await write_audit_chained(
        db_session, actor="llm_routing", action="llm_route_decision", target_type="event", target_id=1,
        after={"route": "local"},
    )
    await write_audit_chained(
        db_session, actor="tester", action="llm_invocation", target_type="event", target_id=1,
        after={"route": "local"},
    )
    await write_audit_chained(
        db_session, actor="tester", action="llm_invocation", target_type="event", target_id=2,
        after={"route": "remote"},
    )
    rows = await _chain_rows(db_session)
    assert len(rows) == 3

    tampered = json.loads(rows[1].after_json)
    tampered["route"] = "remote"  # 篡改中间记录的业务字段，chain 块原样保留
    rows[1].after_json = json.dumps(tampered, ensure_ascii=False)
    await db_session.commit()

    result = await verify_compliance_chain(db_session)
    assert result["ok"] is False
    assert result["broken_id"] == rows[1].id


# ---------------- 5. ingest 入站脱敏集成 ----------------


@pytest.mark.asyncio
async def test_ingest_masks_before_persist_by_default(db_session):
    body = IngestAlertBody(
        source="ump",
        event_id="E2E-MASK-1",
        service_name="payments",
        raw_message="card 6222020200112233 phone 13812345678 from 10.1.2.3",
        severity="warning",
    )
    result = await _ingest_one(db_session, body)
    assert result["result"] == "accepted"

    row = (
        await db_session.execute(select(Events).where(Events.event_id == "E2E-MASK-1"))
    ).scalar_one()
    assert "6222****2233" in row.raw_log
    assert "138****5678" in row.raw_log
    assert "10.1.2.x" in row.raw_log
    assert "6222020200112233" not in row.raw_log
    assert "13812345678" not in row.raw_log
    assert "10.1.2.3" not in row.raw_log

    audit = (
        await db_session.execute(
            select(Audit_logs).where(Audit_logs.action == "event_ingest").order_by(Audit_logs.id.desc()).limit(1)
        )
    ).scalar_one()
    after = json.loads(audit.after_json)
    assert after["data_masking_enabled"] is True
    assert after["masked"] is True
    assert after["sensitivity_categories"], "掩码占位符应被二次识别为敏感"


@pytest.mark.asyncio
async def test_ingest_masking_disabled_keeps_raw(db_session):
    """开关显式关闭：原文落库（部署方显式选择，审计可见 data_masking_enabled=false）。"""
    await _set_config(db_session, "data_masking_enabled", "false")
    body = IngestAlertBody(
        source="ump",
        event_id="E2E-MASK-2",
        service_name="payments",
        raw_message="card 6222020200112233 phone 13812345678",
        severity="warning",
    )
    result = await _ingest_one(db_session, body)
    assert result["result"] == "accepted"

    row = (
        await db_session.execute(select(Events).where(Events.event_id == "E2E-MASK-2"))
    ).scalar_one()
    assert "6222020200112233" in row.raw_log and "13812345678" in row.raw_log

    audit = (
        await db_session.execute(
            select(Audit_logs).where(Audit_logs.action == "event_ingest").order_by(Audit_logs.id.desc()).limit(1)
        )
    ).scalar_one()
    after = json.loads(audit.after_json)
    assert after["data_masking_enabled"] is False
    assert after["masked"] is False
    # 原文场景兜底：敏感模式仍可识别（路由依据不因开关关闭而丢失）
    assert "phone_number" in after["sensitivity_categories"]


# ---------------- 6. 路由决策不随诊断失败丢失 ----------------


@pytest.mark.asyncio
async def test_route_decision_audit_survives_diagnosis_failure(db_session, monkeypatch):
    """诊断失败（无效 JSON → 502）时，路由决策链审计仍已落库可追溯。"""
    _seed_payment_kb(db_session)
    event = _sensitive_event("EV-SENS-3")
    db_session.add(event)
    await db_session.commit()
    await _set_config(db_session, "llm_local_base_url", "http://ollama:11434/v1")
    await _set_config(db_session, "llm_local_model", "qwen2.5:14b")

    stub = RouteRecordingLLM("这不是 JSON 输出 {")
    monkeypatch.setattr(llm_runtime, "llm_chat", stub)

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        await run_diagnosis(db_session, event.id, "tester")
    assert exc_info.value.status_code == 502
    assert stub.routes == ["local", "local"]  # 两次尝试均以本地路由发起

    rows = await _chain_rows(db_session)
    decisions = [json.loads(r.after_json) for r in rows if r.action == "llm_route_decision"]
    assert decisions and decisions[-1]["route"] == "local"
    for item in decisions:
        assert item["chain"]["hash"]
