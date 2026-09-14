"""L4 LLM Listwise 重排：让大模型以"全局视野"对 Top-5 做最终裁决。

与 L3 Pointwise 打分不同，Listwise 让 LLM 一次性看到全部候选并输出全局排序，
能捕捉"案例 A 根因更贴近当前告警"这类需要跨案例比较的判断。

工程保障（L4 是加分项，不是必需项）：
- LCEL 链：ChatPromptTemplate | ChatOpenAI(temperature=0) | JSON 解析；
- 幻觉校验：过滤未知 case_id、按原顺序补全遗漏案例；
- rank→score：线性衰减 (n-i)/n，Top-1 得 1.0；
- 任何失败（无 API Key / JSON 非法 / 超时 / 异常）都原样透传并标记 _l4_available=False，
  编排层据此回退 L3/L2 融合权重。
"""
from typing import Any, Dict, List, Optional, Union

from langchain_core.documents import Document
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableLambda

from ..utils.logger import get_logger

logger = get_logger(__name__)

LISTWISE_PROMPT = """你是资深 SRE 专家，负责从历史故障案例中为当前告警挑选最有参考价值的方案。

【当前告警】
{query}

【候选案例】
{candidates}

请综合三个维度判断：1) 根因与故障机理的相关性；2) 服务与拓扑场景的相似度；3) 处置方案对当前告警的可复用性。
只输出如下 JSON（禁止输出其他内容），按参考价值从高到低排列全部候选：
{{"ranking": [{{"case_id": "候选列表中真实存在的 ID", "reason": "不超过30字的排序理由"}}]}}"""


class LLMListwiseReranker:
    """LLM Listwise 重排器（LCEL 链 + 幻觉校验 + 可降级）。"""

    def __init__(self, cfg: Dict[str, Any], llm_client: Any = None):
        self.final_k = int(cfg.get("final_k", 3))
        self.llm_client = llm_client
        self._chain = None

    # ---------- LCEL 链构建 ----------

    def _resolve_chat_model(self) -> Optional[Any]:
        """复用主流水线的 ChatOpenAI（避免重复建连与配置漂移）。"""
        llm = self.llm_client
        if llm is None:
            return None
        if hasattr(llm, "get_chat_model"):
            return llm.get_chat_model()
        if hasattr(llm, "_get_llm"):
            return llm._get_llm()
        return llm  # 直接传入 LangChain ChatModel

    def _get_chain(self):
        """惰性构建：prompt | llm | JSON 解析（复用 LLMClient.parse_json 的容错）。"""
        if self._chain is None:
            chat_model = self._resolve_chat_model()
            if chat_model is None:
                return None
            parse = RunnableLambda(lambda msg: _parse_ranking(getattr(msg, "content", msg)))
            self._chain = (
                ChatPromptTemplate.from_messages(
                    [("human", LISTWISE_PROMPT)]
                )
                | chat_model
                | parse
            )
        return self._chain

    # ---------- 主入口 ----------

    def rerank(self, docs: List[Document], query: str) -> List[Document]:
        """对 L2 输出做 Listwise 重排；失败时原样透传（降级语义）。"""
        if not docs or not query:
            self.mark_degraded(docs)
            return list(docs)
        chain = None
        try:
            chain = self._get_chain()
        except Exception as exc:  # noqa: BLE001  API Key 缺失等构造期错误
            logger.warning("L4 chain init failed, fallback to L3: %s", exc)
        if chain is None:
            self.mark_degraded(docs)
            return list(docs)

        try:
            ranking: List[Dict[str, Any]] = chain.invoke(
                {"query": query, "candidates": _format_candidates(docs)}
            )
        except Exception as exc:  # noqa: BLE001  网络/超时/非法输出
            logger.warning("L4 listwise rerank failed, fallback to L3: %s", exc)
            self.mark_degraded(docs)
            return list(docs)

        if not ranking:
            # JSON 合法但无有效排序（截断/空输出）：按降级处理，不污染融合权重
            self.mark_degraded(docs)
            return list(docs)

        return self._validate_and_score(docs, ranking)

    # ---------- 校验与打分 ----------

    @staticmethod
    def _validate_and_score(docs: List[Document], ranking: List[Dict[str, Any]]) -> List[Document]:
        """幻觉校验：过滤未知 ID、补全遗漏，然后 rank→score 线性衰减。"""
        by_id: Dict[str, Document] = {}
        for d in docs:
            by_id.setdefault(str(d.metadata.get("case_id", "")), d)

        ordered: List[Document] = []
        seen: set = set()
        for item in ranking or []:
            case_id = str(item.get("case_id", ""))
            if case_id in by_id and case_id not in seen:
                doc = by_id[case_id]
                doc.metadata["_l4_reason"] = str(item.get("reason", "") or "")[:120]
                ordered.append(doc)
                seen.add(case_id)
        # 遗漏案例按原顺序补在末尾（防 LLM 偷懒截断）
        ordered.extend(d for d in docs if str(d.metadata.get("case_id", "")) not in seen)

        n = len(ordered)
        for i, doc in enumerate(ordered):
            # rank -> score 线性衰减：Top-1 得 1.0，末位得 1/n，任意 n 下均归一化到 (0,1]
            doc.metadata["_l4_score"] = round((n - i) / n, 4)
            doc.metadata["_l4_available"] = True
        if logger.isEnabledFor(10):  # DEBUG
            logger.debug("L4 ranking: %s", [d.metadata.get("case_id") for d in ordered])
        return ordered

    @staticmethod
    def mark_degraded(docs: List[Document]) -> None:
        for d in docs:
            d.metadata["_l4_available"] = False


def _format_candidates(docs: List[Document]) -> str:
    """为 Listwise Prompt 渲染候选清单（每条不超过 3 行，控制 token 预算）。"""
    lines: List[str] = []
    for idx, d in enumerate(docs, 1):
        md = d.metadata
        lines.append(
            f"{idx}. case_id={md.get('case_id', '')} | {md.get('service_name', '')}"
            f" | 现象: {str(md.get('alert_template', ''))[:120]}"
            f" | 根因: {str(md.get('root_cause', ''))[:100]}"
            f" | 方案: {str(md.get('solution', ''))[:100]}"
        )
    return "\n".join(lines)


def _parse_ranking(text: Any) -> List[Dict[str, Any]]:
    """解析 LLM 输出中的 ranking 数组（容忍 markdown 围栏）。"""
    from .llm_client import LLMClient

    if not isinstance(text, str):
        return []
    data: Optional[dict] = LLMClient.parse_json(text)
    if not data:
        return []
    ranking = data.get("ranking")
    return ranking if isinstance(ranking, list) else []
