"""单轮诊断墙钟预算（Cloudflare 502 防护）专项测试。

覆盖 console_ai.run_diagnosis 的预算语义与 console_agent._remaining_llm_timeout 的截断规则：
- _remaining_llm_timeout：deadline 为 None 不设限；剩余充足取配置超时；
  剩余紧张按"剩余预算-预留"截断（下限 DIAGNOSE_MIN_ROUND_SECONDS）；
- 预算耗尽：单轮诊断在发起 LLM 调用前返回结构化 502（diagnose_time_budget_exhausted），
  事件落 degraded_reason，且未消耗任何 LLM 调用；
- 降级继承：Agent 降级传入的 deadline（剩余预算）同样生效，先于 LLM 调用触发 502。
"""

import time

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.database import Base
from models import Events as events_module  # noqa: F401  注册表
from models import kb_cases  # noqa: F401  注册表
from models.Events import Events
from models.kb_cases import Kb_cases
from schemas.aihub import GenTxtResponse
from services import console_ai, llm_runtime
from services.console_agent import (
    DIAGNOSE_MIN_ROUND_SECONDS,
    DIAGNOSE_TIME_RESERVE_SECONDS,
    _remaining_llm_timeout,
)


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


async def _seed_event_with_case(session: AsyncSession) -> Events:
    """种子一条待诊断事件与一个可匹配知识案例（保证进入 LLM 阶段而非 unknown 分支）。"""
    event = Events(
        event_id="EV-BUDGET-1",
        template="OOMKilled",
        error_type="memory",
        service_name="orders",
        cluster="c1",
        severity="critical",
        raw_log="OOMKilled pod orders",
        topology="",
        status="pending",
    )
    session.add(event)
    session.add(
        Kb_cases(
            case_id="kb-budget",
            error_type="memory",
            service_name="orders",
            cluster="c1",
            alert_template="OOMKilled",
            root_cause="容器内存超限",
            solution="调大内存限制",
            topology_snapshot="",
            status="active",
            version=1,
            feedback_score=0,
        )
    )
    await session.commit()
    return event


class _CountingLLM:
    """LLM 计数桩：记录调用次数（预算测试中预期为 0 次调用）。"""

    def __init__(self):
        self.calls = 0

    async def __call__(self, db, messages, **kwargs) -> GenTxtResponse:
        self.calls += 1
        return GenTxtResponse(content="{}", model="stub", usage=None)


def test_remaining_llm_timeout_semantics():
    """超时截断规则：无 deadline 不设限；充足取配置值；紧张按剩余预算截断（有下限）。"""
    assert _remaining_llm_timeout(None, 45) is None
    # 剩余 100s（now = deadline - 100）：min(45, 88) = 45（配置超时生效）
    assert _remaining_llm_timeout(1000.0, 45, now=900.0) == 45
    # 剩余 100s、配置 300s：min(300, 88) = 88（剩余预算-预留生效）
    assert _remaining_llm_timeout(1000.0, 300, now=900.0) == 88
    # 剩余 15s：max(8, 3) = 8（下限保证调用仍可发起且整体耗时有界）
    assert _remaining_llm_timeout(1000.0, 45, now=985.0) == int(DIAGNOSE_MIN_ROUND_SECONDS)
    assert DIAGNOSE_MIN_ROUND_SECONDS < DIAGNOSE_TIME_RESERVE_SECONDS


@pytest.mark.asyncio
async def test_single_round_budget_exhausted_returns_structured_502(db_session, monkeypatch):
    """预算耗尽：单轮诊断先于 LLM 调用返回结构化 502，事件落 degraded_reason。"""
    event = await _seed_event_with_case(db_session)
    fake = _CountingLLM()
    monkeypatch.setattr(llm_runtime, "llm_chat", fake)
    # 放大"发起一次调用所需最小剩余预算"，使预算检查在首轮即触发（确定性，不依赖真实时钟）
    monkeypatch.setattr(console_ai, "_SINGLE_MIN_LLM_SECONDS", 3600.0)

    with pytest.raises(HTTPException) as exc_info:
        await console_ai.run_diagnosis(db_session, event.id, "tester")

    assert exc_info.value.status_code == 502
    assert "diagnose_time_budget_exhausted" in str(exc_info.value.detail)
    assert fake.calls == 0
    await db_session.refresh(event)
    assert event.degraded_reason == "diagnose_time_budget_exhausted"
    assert event.status != "diagnosed"


@pytest.mark.asyncio
async def test_single_round_inherits_agent_deadline(db_session, monkeypatch):
    """降级继承：Agent 传入的剩余预算（deadline）生效，先于 LLM 调用触发 502。"""
    event = await _seed_event_with_case(db_session)
    fake = _CountingLLM()
    monkeypatch.setattr(llm_runtime, "llm_chat", fake)

    with pytest.raises(HTTPException) as exc_info:
        await console_ai.run_diagnosis(
            db_session, event.id, "tester", deadline=time.perf_counter() + 0.1
        )

    assert exc_info.value.status_code == 502
    assert "diagnose_time_budget_exhausted" in str(exc_info.value.detail)
    assert fake.calls == 0
    await db_session.refresh(event)
    assert event.degraded_reason == "diagnose_time_budget_exhausted"
