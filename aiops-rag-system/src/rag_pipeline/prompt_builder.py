"""Few-shot Prompt 构建器：基于 LangChain ChatPromptTemplate 渲染 SRE 根因推理提示。

LangChain 化改造：PromptTemplate -> ChatPromptTemplate（chat 模型原生消息格式）。
build_prompt 仍返回字符串（流水线 llm.invoke(prompt) 接口不变），
渲染结果为 Human 消息 content，与 LCEL 诊断链的输出契约一致。
"""
from typing import Dict, List

from langchain_core.prompts import ChatPromptTemplate

from ..models.event import StandardizedEvent

PROMPT_TEMPLATE = """【角色设定】你是一位精通分布式系统、Redis、MySQL 的 SRE 专家。
【核心任务】根据当前告警和提供的"历史相似故障案例"，进行根因推理。必须按以下 JSON 格式输出（不要输出任何其他内容）：
{{
  "root_cause_service": "推测的根本原因服务",
  "root_cause_type": "故障类型（如连接池耗尽/慢查询/网络分区）",
  "evidence": ["关键证据1", "关键证据2"],
  "confidence": 0.95,
  "suggest_actions": ["具体修复命令1", "具体修复命令2"]
}}

{few_shot_cases}

【当前待诊断告警】
- 服务: {service_name}
- 标准化类型: {error_type}
- 典型日志模板: {alert_template}
- 下游依赖: {downstream}
"""

_TEMPLATE = ChatPromptTemplate.from_messages([("human", PROMPT_TEMPLATE)])


def _format_time(ts_ms: int, current_time_ms: int) -> str:
    try:
        days = (current_time_ms - ts_ms) / (1000 * 3600 * 24)
        if days >= 1:
            return f"{days:.0f}天前"
        return f"{max(0, days * 24):.0f}小时前"
    except (TypeError, ValueError):
        return "未知时间"


def _format_case(idx: int, case: Dict, current_event: StandardizedEvent) -> str:
    """渲染单个参考案例块：综合相关度优先取融合分，回退召回距离。"""
    related = case.get("_final_score", case.get("distance", 0.5))
    when = _format_time(int(case.get("start_time", 0)), current_event.timestamp)
    # L4 排序理由（可用时附带，帮助 LLM 理解案例入选依据）
    reason = str(case.get("_l4_reason", "") or "")
    reason_line = f"\n- 入选理由: {reason}" if reason else ""
    return (
        f"【参考案例{idx}】（综合相关度: {related:.2f} | 发生时间: {when}）\n"
        f"- 故障特征: {case.get('alert_template', '无')}\n"
        f"- 根因: {case.get('root_cause', '无')}\n"
        f"- 解决方案: {case.get('solution', '无')}{reason_line}"
    )


def build_prompt(current_event: StandardizedEvent, similar_cases: List[Dict]) -> str:
    """将当前事件与 Top K 相似案例渲染为完整的 Few-shot Prompt（Human 消息文本）。"""
    blocks = [_format_case(i, case, current_event) for i, case in enumerate(similar_cases, 1)]
    messages = _TEMPLATE.format_messages(
        few_shot_cases="\n\n".join(blocks) if blocks else "（无历史相似案例）",
        service_name=current_event.service_name,
        error_type=current_event.error_type,
        alert_template=current_event.template,
        downstream=current_event.topology.get("downstream", []) or ["未知"],
    )
    return messages[0].content
