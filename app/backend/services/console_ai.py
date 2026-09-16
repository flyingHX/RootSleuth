"""AI 诊断能力：业务 RAG 召回 + Embedding 语义加分 + 控制台可配置 LLM 结构化根因诊断。

流程：
1. 读取事件与知识案例（读阶段，随后提交关闭事务）；
2. 按业务规则召回 top-N 相似案例（error_type / service / cluster / 模板关键词）；
3. 若配置中心启用 Embedding（embedding_model 等），追加语义向量相似度加分重排；
4. 通过 llm_runtime（模型/温度/接入方式由控制台配置中心驱动）生成 JSON 诊断，
   带超时与一次修复重试；
5. 持久化诊断结果到事件表并写审计。

LLM 默认走平台内置 AIHub（llm_provider=atoms_hub），管理员可在配置中心切换为
自建 OpenAI 兼容接口（openai_compatible + base_url/api_key/模型名），变更立即生效。
"""
import asyncio
import json
import logging
import re
import time
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.Events import Events
from models.kb_cases import Kb_cases
from schemas.aihub import ChatMessage
from services import llm_runtime
from services.console_common import get_config, write_audit, now_iso
from services.quality_scan import evaluate_diagnosis_quality

logger = logging.getLogger(__name__)

DIAGNOSIS_SYSTEM_PROMPT = """你是资深 SRE 根因诊断助手。基于给定的告警信息和相似历史案例，输出严格的 JSON 诊断结果。

要求：
- 只输出一个 JSON 对象，不要输出任何解释、Markdown 代码块或多余文本。
- JSON 字段：root_cause（字符串，根因分析，中文）、solution（字符串，处置建议，中文）、confidence（0 到 1 之间的小数）、command（字符串，可直接复制执行的处置命令，若无可给空字符串）。
- root_cause 与 solution 必须结合历史案例与告警上下文给出，不得空洞。
"""

_UNKNOWN_RAG_STATUS = "unknown"
_DEGRADED_RAG_STATUS = "degraded"


def score_case(case: Kb_cases, event: Events) -> float:
    """演示版业务重排：多信号加权评分。"""
    score = 0.0
    if case.error_type and case.error_type == event.error_type:
        score += 3.0
    if case.service_name and event.service_name and case.service_name == event.service_name:
        score += 2.0
    if case.cluster and case.cluster == event.cluster:
        score += 0.5
    if case.topology_snapshot and event.topology and case.topology_snapshot == event.topology:
        score += 0.5
    template_tokens = _template_tokens(event.template or "")
    case_tokens = set(_template_tokens(case.alert_template or ""))
    overlap = len(template_tokens & case_tokens)
    if overlap:
        score += min(2.0, overlap * 0.5)
    if case.feedback_score:
        score += max(-0.5, min(0.5, case.feedback_score * 0.25))
    return score


def _template_tokens(template: str) -> set:
    tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_\-]{2,}", template or "")
    return set(tokens)


def build_candidates(cases: List[Kb_cases], event: Events, top_n: int = 3) -> List[Dict[str, Any]]:
    """召回并排序 top-N 相似案例，score 归一化到 0~1。"""
    scored = [(score_case(case, event), case) for case in cases]
    scored = [item for item in scored if item[0] > 0]
    scored.sort(key=lambda item: item[0], reverse=True)
    candidates: List[Dict[str, Any]] = []
    for raw_score, case in scored[:top_n]:
        candidates.append(
            {
                "case_id": case.case_id,
                "error_type": case.error_type,
                "service_name": case.service_name,
                "score": round(min(0.99, raw_score / 6.0), 4),
                "root_cause": case.root_cause,
                "solution": case.solution,
                "feedback_score": case.feedback_score,
            }
        )
    return candidates


def _embed_text_for_event(event: Events) -> str:
    """构造告警侧的 Embedding 输入文本。"""
    return f"{event.template or ''}\n{event.service_name or ''}\n{event.raw_log or ''}".strip()[:1000]


def _embed_text_for_case(case: Kb_cases) -> str:
    """构造知识案例侧的 Embedding 输入文本。"""
    return (
        f"{case.alert_template or ''}\n{case.error_type or ''}\n"
        f"{case.service_name or ''}\n{case.root_cause or ''}"
    ).strip()[:1000]


async def apply_embedding_rerank(
    db: AsyncSession, event: Events, cases: List[Kb_cases], candidates: List[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """Embedding 语义加分：对业务重排后的候选案例计算余弦相似度并加权。

    - 未配置 Embedding（embedding_model 为空）时返回 None，保持纯业务重排；
    - 调用失败静默降级（不阻断诊断主链路）；
    - 加分权重 0.5 * cosine，score 仍归一化到 0~0.99 并重新排序。
    """
    if not candidates:
        return None
    candidate_ids = {cand["case_id"] for cand in candidates}
    ranked_cases = [case for case in cases if case.case_id in candidate_ids]
    vectors = await llm_runtime.embed_texts(
        db,
        [_embed_text_for_event(event)] + [_embed_text_for_case(case) for case in ranked_cases],
    )
    if not vectors or len(vectors) != len(ranked_cases) + 1:
        return None
    base_vector = vectors[0]
    vector_by_case = {case.case_id: vectors[i + 1] for i, case in enumerate(ranked_cases)}
    for cand in candidates:
        sim = llm_runtime.cosine_similarity(base_vector, vector_by_case.get(cand["case_id"], []))
        if sim > 0:
            cand["embedding_score"] = round(sim, 4)
            cand["score"] = round(min(0.99, cand["score"] + 0.5 * sim), 4)
    candidates.sort(key=lambda item: item["score"], reverse=True)
    return {"applied": True, "boost_weight": 0.5, "reranked": True}


def build_messages(event: Events, candidates: List[Dict[str, Any]]) -> List[ChatMessage]:
    candidates_text = json.dumps(candidates, ensure_ascii=False, indent=2)
    user_content = f"""告警信息：
- event_id: {event.event_id}
- severity: {event.severity}
- service: {event.service_name}
- cluster: {event.cluster}
- error_type: {event.error_type or "unknown"}
- 标准化模板: {event.template or "N/A"}
- 原始日志: {event.raw_log or "N/A"}
- 拓扑: {event.topology or "N/A"}

RAG 召回的相似历史案例（已按业务重排）：
{candidates_text}

请输出 JSON 诊断结果（仅 JSON）。"""
    return [
        ChatMessage(role="system", content=DIAGNOSIS_SYSTEM_PROMPT),
        ChatMessage(role="user", content=user_content),
    ]


def extract_json_payload(text: str) -> Optional[Dict[str, Any]]:
    """从模型输出中提取 JSON 对象，容忍 Markdown 代码块包裹。"""
    if not text:
        return None
    cleaned = text.strip()
    fence_match = re.search(r"```(?:json)?\s*(.*?)```", cleaned, flags=re.DOTALL)
    if fence_match:
        cleaned = fence_match.group(1).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        payload = json.loads(cleaned[start : end + 1])
    except (TypeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def validate_diagnosis(payload: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """校验诊断 JSON 必填字段，失败返回 None。"""
    if not payload:
        return None
    root_cause = payload.get("root_cause")
    solution = payload.get("solution")
    confidence = payload.get("confidence")
    if not isinstance(root_cause, str) or not root_cause.strip():
        return None
    if not isinstance(solution, str) or not solution.strip():
        return None
    try:
        confidence_num = float(confidence)
    except (TypeError, ValueError):
        return None
    if not (0.0 <= confidence_num <= 1.0):
        return None
    command = payload.get("command")
    return {
        "root_cause": root_cause.strip(),
        "solution": solution.strip(),
        "confidence": round(confidence_num, 4),
        "command": command.strip() if isinstance(command, str) else "",
    }


def _apply_rag_fields(
    event: Events,
    rag_status: str,
    rag_score: Optional[float],
    rag_ms: float,
    degraded_reason: Optional[str],
    candidates: List[Dict[str, Any]],
) -> None:
    event.rag_status = rag_status
    event.rag_score = rag_score
    event.rag_ms = round(rag_ms, 2)
    event.degraded_reason = degraded_reason
    event.candidates_json = json.dumps(candidates, ensure_ascii=False)


async def run_diagnosis(db: AsyncSession, event_id: int, actor: str) -> Dict[str, Any]:
    """执行完整诊断闭环。AI 失败时持久化降级原因并抛出可重试错误。"""
    total_started = time.perf_counter()
    result = await db.execute(select(Events).where(Events.id == event_id))
    event = result.scalar_one_or_none()
    if event is None:
        raise HTTPException(status_code=404, detail="事件不存在")

    cases_result = await db.execute(select(Kb_cases).where(Kb_cases.status != "archived"))
    cases = list(cases_result.scalars().all())

    started = time.perf_counter()
    candidates = build_candidates(cases, event)
    embedding_meta = await apply_embedding_rerank(db, event, cases, candidates)
    rag_ms = (time.perf_counter() - started) * 1000.0

    if not candidates:
        _apply_rag_fields(event, _UNKNOWN_RAG_STATUS, None, rag_ms, "no_similar_case", [])
        event.status = "unknown"
        await db.commit()
        await write_audit(
            db,
            actor=actor,
            action="diagnosis_run",
            target_type="event",
            target_id=event.id,
            after={"rag_status": _UNKNOWN_RAG_STATUS, "degraded_reason": "no_similar_case"},
        )
        return {
            "status": _UNKNOWN_RAG_STATUS,
            "message": "知识库中没有相似案例，已记录为未知告警，可先补充知识案例或晋升规则。",
            "event_id": event.id,
            "rag": {"status": _UNKNOWN_RAG_STATUS, "score": None, "ms": round(rag_ms, 2), "candidates": []},
            "quality": None,
            "elapsed_ms": int((time.perf_counter() - total_started) * 1000),
            "diagnosis": None,
        }

    # 读阶段结束，提交关闭事务后再调用慢速外部 AI 服务。
    _apply_rag_fields(event, "success", candidates[0]["score"], rag_ms, None, candidates)
    await db.commit()
    top_score = candidates[0]["score"]

    timeout_seconds = int(await get_config(db, "llm_timeout_seconds", "45") or 45)
    model_name = await llm_runtime.get_llm_model_name(db)
    messages = build_messages(event, candidates)
    diagnosis: Optional[Dict[str, Any]] = None
    last_error = ""
    try:
        for attempt in range(2):
            try:
                response = await llm_runtime.llm_chat(
                    db,
                    messages,
                    max_tokens=1200,
                    timeout=timeout_seconds,
                )
                payload = extract_json_payload(response.content)
                diagnosis = validate_diagnosis(payload)
                if diagnosis is not None:
                    break
                last_error = "模型输出不是合法的诊断 JSON"
                messages = list(messages) + [
                    ChatMessage(
                        role="user",
                        content=(
                            f"你上一次的输出无法通过 JSON 校验（{last_error}）。"
                            "请重新输出，且只输出一个符合字段要求的 JSON 对象。"
                        ),
                    )
                ]
            except asyncio.TimeoutError:
                raise HTTPException(
                    status_code=504,
                    detail=f"LLM 诊断超时（>{timeout_seconds}s），请稍后重试或调大 llm_timeout_seconds 配置",
                )
    except HTTPException:
        event.rag_status = _DEGRADED_RAG_STATUS
        event.degraded_reason = "llm_timeout"
        event.confidence = None
        event.ai_root_cause = None
        await db.commit()
        raise
    except Exception as exc:  # noqa: BLE001 - AI 调用失败必须显式可重试
        logger.error("Diagnosis LLM call failed: %s", exc)
        event.rag_status = _DEGRADED_RAG_STATUS
        event.degraded_reason = f"llm_error: {type(exc).__name__}"
        await db.commit()
        raise HTTPException(status_code=502, detail=f"AI 诊断调用失败：{exc}，请点击重试")

    if diagnosis is None:
        event.rag_status = _DEGRADED_RAG_STATUS
        event.degraded_reason = f"invalid_json_output: {last_error}"
        await db.commit()
        raise HTTPException(status_code=502, detail=f"AI 诊断输出校验失败（{last_error}），请点击重试")

    threshold = float(await get_config(db, "confidence_threshold", "0.75") or 0.75)
    # 单次诊断质量指标（在线评估，前端逐次展示 + 总览聚合的数据来源）
    quality = evaluate_diagnosis_quality(
        {
            "template": event.template or "",
            "error_type": event.error_type or "",
            "service_name": event.service_name or "",
        },
        f"{diagnosis['root_cause']}\n{diagnosis['solution']}",
        candidates,
    )
    event.ai_root_cause = diagnosis["root_cause"]
    event.ai_solution = diagnosis["solution"]
    event.ai_command = diagnosis.get("command") or None
    event.ai_output_json = json.dumps({**diagnosis, "quality": quality}, ensure_ascii=False)
    event.confidence = diagnosis["confidence"]
    event.status = "diagnosed"
    if diagnosis["confidence"] < threshold:
        event.degraded_reason = f"low_confidence(<{threshold})"
    else:
        event.degraded_reason = None
    await db.commit()

    elapsed_ms = int((time.perf_counter() - total_started) * 1000)
    await write_audit(
        db,
        actor=actor,
        action="diagnosis_run",
        target_type="event",
        target_id=event.id,
        after={
            "model": model_name,
            "rag_score": top_score,
            "embedding_boost": bool(embedding_meta),
            "confidence": diagnosis["confidence"],
            "low_confidence": diagnosis["confidence"] < threshold,
            "trust_index": quality["trust_index"],
            "quality_ok": quality["quality_ok"],
            "elapsed_ms": elapsed_ms,
        },
    )

    return {
        "status": "success",
        "message": "诊断完成",
        "event_id": event.id,
        "rag": {
            "status": "success",
            "score": top_score,
            "ms": round(rag_ms, 2),
            "candidates": candidates,
            "embedding": embedding_meta,
            "rerank": {
                "strategy": "business_signals+embedding_boost" if embedding_meta else "business_signals",
                "candidate_count": len(candidates),
            },
        },
        "quality": quality,
        "elapsed_ms": elapsed_ms,
        "diagnosis": {
            **diagnosis,
            "model": model_name,
            "low_confidence": diagnosis["confidence"] < threshold,
            "threshold": threshold,
        },
        "persisted_at": now_iso(),
    }
