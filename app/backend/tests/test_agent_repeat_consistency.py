"""同一事件重复深度诊断一致性（稳定性闭环）端到端专项测试。

以 SQLite 内存库 + 脚本化 LLM 桩（mock llm_runtime.llm_chat）重复执行 run_diagnose_agent，
逐项断言两次运行的稳定性闭环：

成功路径（ReAct 多轮推理 → finish）：
- input_fingerprint / context_fingerprint 一致；
- snapshot（事件输入指纹、知识候选 case_id 序列、激活规则版本、模型参数）逐块一致；
- 工具轨迹（iteration/thought/tool/args/observation）完整一致；
- 结论（root_cause/solution/confidence/evidence_chain/command）一致；
- 质量指标（faithfulness/trust_index 等全部字段）一致；
- 事件降级状态（status/degraded_reason/confidence）一致；
- diagnose_temperature=0 全链路透传到每次 LLM 调用；
- agent_sessions 结果持久化：succeeded 会话 result_json 中 conclusion/quality/stability 一致。

降级路径（ReAct 失败 → 单轮诊断 fallback）：
- 两次运行 status 均为 degraded，fallback 诊断字段/质量/候选一致；
- event.ai_root_cause / degraded_reason 一致；
- 会话 error_message（agent_failed_to_conclude）一致。
"""

import json
from typing import Any, Dict, List, Optional

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import inspect as sa_inspect, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.database import Base
from models import agent_sessions  # noqa: F401  注册表
from models import audit_logs  # noqa: F401
from models import console_configs  # noqa: F401
from models import kb_cases  # noqa: F401
from models import rule_versions  # noqa: F401
from models import Events as events_module  # noqa: F401
from models.Events import Events
from models.agent_sessions import Agent_sessions
from models.kb_cases import Kb_cases
from models.rule_versions import Rule_versions
from schemas.aihub import GenTxtResponse
from schemas.auth import UserResponse
from services import llm_runtime
from services.console_agent import run_diagnose_agent

RULE_YAML = """rules:
  - id: oom-memory
    error_type: memory
    score: 9
    severity: critical
    keywords: ["OOMKilled", "memory"]
"""


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


def _seed_event(session: AsyncSession) -> Events:
    event = Events(
        event_id="EV-DET-1",
        template="OOMKilled",
        error_type="memory",
        service_name="orders",
        cluster="c1",
        severity="critical",
        raw_log="OOMKilled pod orders Memory cgroup limit exceeded node 10.0.0.5",
        topology="",
        status="pending",
    )
    session.add(event)
    session.add(
        Kb_cases(
            case_id="kb-a",
            error_type="memory",
            service_name="orders",
            cluster="c1",
            alert_template="OOMKilled",
            root_cause="容器内存超限被 cgroup 杀掉",
            solution="调大内存限制并重启 pod",
            topology_snapshot="",
            status="active",
            version=1,
            feedback_score=1,
        )
    )
    session.add(
        Kb_cases(
            case_id="kb-b",
            error_type="memory",
            service_name="orders",
            cluster="c2",
            alert_template="Memory usage high",
            root_cause="内存使用率持续超阈值",
            solution="扩容并排查泄漏",
            topology_snapshot="",
            status="active",
            version=1,
            feedback_score=0,
        )
    )
    session.add(
        Rule_versions(version=1, status="active", content=RULE_YAML, change_note="seed", created_by="test")
    )
    return event


class ScriptedLLM:
    """脚本化 LLM 桩：按调用序号返回预设内容，并记录每次调用的 temperature。"""

    def __init__(self, responses: List[str]):
        self.responses = responses
        self.calls = 0
        self.temperatures: List[Optional[float]] = []

    async def __call__(self, db, messages, **kwargs) -> GenTxtResponse:
        idx = min(self.calls, len(self.responses) - 1)
        self.calls += 1
        self.temperatures.append(kwargs.get("temperature"))
        return GenTxtResponse(content=self.responses[idx], model="scripted-model", usage=None)


SUCCESS_SCRIPT = [
    json.dumps({"thought": "先获取告警详情", "action": {"tool": "get_alert_detail", "args": {}}}, ensure_ascii=False),
    json.dumps(
        {
            "thought": "按错误类型与服务检索知识库",
            "action": {"tool": "search_kb", "args": {"error_type": "memory", "service_name": "orders"}},
        },
        ensure_ascii=False,
    ),
    json.dumps(
        {
            "thought": "证据足够，输出结论",
            "finish": True,
            "result": {
                "root_cause": "orders 服务容器内存超限被 cgroup 杀掉",
                "solution": "调大内存限制并重启 pod",
                "confidence": 0.9,
                "evidence_chain": [
                    "告警模板 OOMKilled 与知识案例 kb-a 根因一致",
                    "orders 服务告警日志样本同为 OOMKilled",
                ],
                "command": "",
            },
        },
        ensure_ascii=False,
    ),
]

DEGRADED_SCRIPT = ["这不是 JSON 输出 {"] * 10 + [
    json.dumps(
        {"root_cause": "orders 服务内存超限", "solution": "扩容内存并重启服务", "confidence": 0.8, "command": ""},
        ensure_ascii=False,
    )
]


async def _session_rows(session: AsyncSession, event_pk: int) -> List[Agent_sessions]:
    result = await session.execute(
        select(Agent_sessions)
        .where(Agent_sessions.event_id == event_pk)
        .order_by(Agent_sessions.id.asc())
    )
    return list(result.scalars().all())


def _event_snapshot(event: Events) -> Dict[str, Any]:
    """捕获事件行全部列值，用于第二次运行前恢复同一输入。"""
    return {attr.key: getattr(event, attr.key) for attr in sa_inspect(event).mapper.column_attrs}


async def _restore_event(session: AsyncSession, event: Events, snap: Dict[str, Any]) -> None:
    """恢复事件快照：诊断回写（status/ai_output_json/ai_root_cause 等）是有意的副作用，
    但会让第二次运行的 get_alert_detail 观察到"既往诊断"提示；重复一致性测试必须先复位输入。"""
    for key, value in snap.items():
        setattr(event, key, value)
    await session.commit()
    await session.refresh(event)


@pytest.mark.asyncio
async def test_repeat_diagnose_agent_success_consistency(db_session, monkeypatch):
    """成功路径：两次深度诊断的指纹/快照/轨迹/结论/质量/降级状态完全一致。"""
    event = _seed_event(db_session)
    await db_session.commit()

    user = UserResponse(id="u-1", email="ops@example.com")
    snap0 = _event_snapshot(event)
    fake1 = ScriptedLLM(SUCCESS_SCRIPT)
    monkeypatch.setattr(llm_runtime, "llm_chat", fake1)
    run1 = await run_diagnose_agent(db_session, user, event.id)
    # 同一输入是重复一致性的前提：恢复首次运行前的事件快照后再跑第二次
    await _restore_event(db_session, event, snap0)
    fake2 = ScriptedLLM(SUCCESS_SCRIPT)
    monkeypatch.setattr(llm_runtime, "llm_chat", fake2)
    run2 = await run_diagnose_agent(db_session, user, event.id)

    assert run1["status"] == run2["status"] == "success"
    a1, a2 = run1["agent"], run2["agent"]

    # 温度确定性：diagnose_temperature 默认 0 且全链路透传到每次 LLM 调用
    assert a1["stability"]["temperature"] == a2["stability"]["temperature"] == 0.0
    assert fake1.temperatures == [0.0] * fake1.calls
    assert fake2.temperatures == [0.0] * fake2.calls

    # 指纹一致：输入指纹 / 上下文指纹
    assert a1["stability"]["input_fingerprint"] == a2["stability"]["input_fingerprint"]
    assert a1["stability"]["context_fingerprint"] == a2["stability"]["context_fingerprint"] != ""
    assert a1["stability"]["eval_context"] == a2["stability"]["eval_context"]

    # 快照一致：事件输入、知识候选、规则版本、模型参数逐块可比
    snap1, snap2 = a1["stability"]["snapshot"], a2["stability"]["snapshot"]
    assert snap1 == snap2
    assert snap1["event"]["event_id"] == "EV-DET-1"
    assert snap1["event"]["raw_log_sha"] != ""
    assert snap1["kb_candidates"]["local_ids"] == ["kb-a", "kb-b"]
    assert snap1["kb_candidates"]["local_ids"] == snap1["kb_candidates"]["merged_ids"]
    assert snap1["rule_version"] == 1
    assert snap1["model_params"] == {
        "model": "deepseek-v4-flash",
        "temperature": 0.0,
        "timeout_seconds": 45,
        "time_budget_seconds": 90.0,
    }

    # 工具轨迹一致（调用顺序、参数、观察结果逐条可比）
    assert a1["tool_trace"] == a2["tool_trace"]
    assert [s.get("tool") for s in a1["tool_trace"]] == ["get_alert_detail", "search_kb"]

    # 结论 / 质量指标 / 降级状态一致
    assert a1["conclusion"] == a2["conclusion"]
    assert a1["quality"] == a2["quality"]
    assert a1["iterations"] == a2["iterations"] == 3
    assert a1["rag"] == a2["rag"]

    # 事件表降级与结论字段一致
    await db_session.refresh(event)
    assert event.status == "diagnosed"
    assert event.degraded_reason is None
    assert event.confidence == 0.9
    persisted = json.loads(event.ai_output_json)
    assert persisted["stability"] == a1["stability"]
    assert persisted["quality"] == a1["quality"]

    # 会话持久化：输入复位后无既往结论，每次运行各落一条 succeeded（不产生 superseded 归档）
    rows = await _session_rows(db_session, event.id)
    assert [r.status for r in rows] == ["succeeded", "succeeded"]
    result1 = json.loads(rows[0].result_json)
    result2 = json.loads(rows[1].result_json)
    assert rows[0].tool_trace == rows[1].tool_trace
    assert result1["conclusion"] == result2["conclusion"]
    assert result1["quality"] == result2["quality"]
    assert result1["stability"] == result2["stability"]


@pytest.mark.asyncio
async def test_repeat_diagnose_agent_degraded_consistency(db_session, monkeypatch):
    """降级路径：ReAct 失败两次均按同一降级矩阵回退单轮诊断，结果与状态一致。"""
    event = _seed_event(db_session)
    await db_session.commit()

    user = UserResponse(id="u-1", email="ops@example.com")
    snap0 = _event_snapshot(event)
    monkeypatch.setattr(llm_runtime, "llm_chat", ScriptedLLM(DEGRADED_SCRIPT))
    run1 = await run_diagnose_agent(db_session, user, event.id)
    # 同一输入是重复一致性的前提：恢复首次运行前的事件快照后再跑第二次
    await _restore_event(db_session, event, snap0)
    monkeypatch.setattr(llm_runtime, "llm_chat", ScriptedLLM(DEGRADED_SCRIPT))
    run2 = await run_diagnose_agent(db_session, user, event.id)

    # 降级状态与原因一致（agent 多轮失败 → 单轮诊断兜底成功）
    assert run1["status"] == run2["status"] == "degraded"
    assert run1["agent"] is None and run2["agent"] is None
    fb1, fb2 = run1["fallback"], run2["fallback"]
    assert fb1["diagnosis"] == fb2["diagnosis"]
    assert fb1["quality"] == fb2["quality"]
    assert fb1["rag"]["status"] == fb2["rag"]["status"] == "success"
    assert fb1["rag"]["score"] == fb2["rag"]["score"]
    assert fb1["rag"]["candidates"] == fb2["rag"]["candidates"]
    assert [c["case_id"] for c in fb1["rag"]["candidates"]] == ["kb-a", "kb-b"]

    # 事件表降级字段一致
    await db_session.refresh(event)
    assert event.status == "diagnosed"
    assert event.degraded_reason is None
    assert event.ai_root_cause == "orders 服务内存超限"

    # 会话降级原因（loop_error）与持久化结果一致
    rows = await _session_rows(db_session, event.id)
    assert [r.status for r in rows] == ["degraded", "degraded"]
    assert rows[0].error_message == rows[1].error_message == "ValueError: agent_failed_to_conclude"
    result1 = json.loads(rows[0].result_json)
    result2 = json.loads(rows[1].result_json)
    assert result1["fallback"] == result2["fallback"] == "single_round_diagnosis"
    assert result1["fallback_error"] is None and result2["fallback_error"] is None
    assert result1["diagnosis"]["diagnosis"] == result2["diagnosis"]["diagnosis"]
    assert result1["diagnosis"]["quality"] == result2["diagnosis"]["quality"]


@pytest.mark.asyncio
async def test_diagnose_time_budget_exhausted_skips_fallback(db_session, monkeypatch):
    """墙钟预算耗尽：首轮 LLM 调用前即终止推理，跳过 LLM 降级并返回结构化 502。"""
    from services import console_agent as console_agent_module

    event = _seed_event(db_session)
    await db_session.commit()

    user = UserResponse(id="u-1", email="ops@example.com")
    fake = ScriptedLLM(SUCCESS_SCRIPT)
    monkeypatch.setattr(llm_runtime, "llm_chat", fake)

    async def _zero_budget(_db):
        return 0.0

    monkeypatch.setattr(console_agent_module, "_resolve_diagnose_time_budget", _zero_budget)

    with pytest.raises(HTTPException) as exc_info:
        await run_diagnose_agent(db_session, user, event.id)

    assert exc_info.value.status_code == 502
    assert "diagnose_time_budget_exhausted" in str(exc_info.value.detail)
    # 预算在每次推理轮次开始前检查：未消耗任何 LLM 调用
    assert fake.calls == 0
