"""四层业务重排编排：L1 规则过滤 → L2 七特征融合 → (L3 ∥ L4) 并行 → 分数融合。

漏斗形状（召回 Top-20 起）：
  L1 RuleFilter        20 -> ~15   硬规则剔除（<1ms，永不确定失败）
  L2 BusinessReranker  15 -> 10    七特征加权融合
  L3 Cross-Encoder     10 -> 5     BGE 语义精排（不可用时透传）
  L4 LLM Listwise       5 -> 3     全局视野终裁（后台线程，超时/失败降级）
  Score Fusion                    可用层权重重归一化（0.2/0.4/0.4），任一层失效不塌缩

L3/L4 并行策略：L4 只依赖 L2 Top-5，先提交后台线程；L3 在当前线程跑 10->5；
join 时对 L4 施加独立超时（l4_timeout），超时不阻塞主流水线。
"""
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Dict, List

from langchain_core.documents import Document

from ..utils.logger import get_logger
from .documents import document_to_case_dict, rows_to_documents
from .reranker_l1 import RuleFilter
from .reranker_l2 import BusinessReranker
from .reranker_l3 import BGEReranker
from .reranker_l4 import LLMListwiseReranker

logger = get_logger(__name__)

# 常规融合权重：L2 业务特征 0.2 / L3 语义精排 0.4 / L4 LLM 终裁 0.4
FUSION_WEIGHTS: Dict[str, float] = {"l2": 0.2, "l3": 0.4, "l4": 0.4}


@dataclass
class RerankStats:
    """单次重排的分层统计（供 pipeline 写 Prometheus 指标与诊断响应）。"""

    input_count: int = 0
    l1_out: int = 0
    l2_out: int = 0
    l3_out: int = 0
    final_out: int = 0
    l3_available: bool = False
    l4_status: str = "skipped"  # ok / degraded / skipped
    l3_latency_ms: float = 0.0
    l4_latency_ms: float = 0.0
    dropped_l1: int = field(default=0)


@dataclass
class RerankResult:
    """重排产物：Top-K case dict + 分层统计。"""

    cases: List[Dict[str, Any]]
    stats: RerankStats


def _event_field(event: Any, name: str, default: Any = "") -> Any:
    """兼容 StandardizedEvent 对象与 dict 事件（Kafka 消费路径）。"""
    if isinstance(event, dict):
        return event.get(name, default)
    return getattr(event, name, default)


def build_query_text(event: Any) -> str:
    """从告警事件拼装重排 query 文本（L3 Cross-Encoder / L4 Listwise 共用）。

    事件模板字段：StandardizedEvent / Kafka dict 事件为 template，容错回退 alert_template。
    """
    parts = [
        str(
            _event_field(event, "template", "")
            or _event_field(event, "alert_template", "")
            or ""
        ),
        str(_event_field(event, "service_name", "") or ""),
        str(_event_field(event, "error_type", "") or ""),
        str(_event_field(event, "cluster", "") or ""),
    ]
    return " | ".join(p for p in parts if p)


def _event_topology(event: Any) -> Dict[str, List[str]]:
    topo = _event_field(event, "topology", {}) or {}
    return topo if isinstance(topo, dict) else {}


def fuse_final_scores(docs: List[Document]) -> None:
    """可用层权重重归一化融合：_final_score = Σ(w_i * s_i) / Σ(w_i)。

    - L2 恒可用（纯内存计算）；
    - L3 需要 _l3_available=True 且存在 _l3_score；
    - L4 需要 _l4_available=True 且存在 _l4_score。
    """
    for d in docs:
        md = d.metadata
        available: Dict[str, float] = {"l2": float(md.get("_l2_score", 0.0) or 0.0)}
        weights = {"l2": FUSION_WEIGHTS["l2"]}
        if md.get("_l3_available") and "_l3_score" in md:
            available["l3"] = float(md.get("_l3_score", 0.0) or 0.0)
            weights["l3"] = FUSION_WEIGHTS["l3"]
        if md.get("_l4_available") and "_l4_score" in md:
            available["l4"] = float(md.get("_l4_score", 0.0) or 0.0)
            weights["l4"] = FUSION_WEIGHTS["l4"]
        total_w = sum(weights.values())
        md["_final_score"] = round(
            sum(weights[k] * available[k] for k in available) / total_w, 4
        )


class RerankPipeline:
    """L1~L4 编排器。pipeline.py 只需调用 rerank(rows, event, now_ms)。"""

    def __init__(self, config: Dict[str, Any], llm_client: Any = None):
        cfg: Dict[str, Any] = (config or {}).get("rerank", {}) if isinstance(config, dict) else {}
        self.cfg = cfg
        self.l1 = RuleFilter(cfg)
        self.l2 = BusinessReranker(
            top_k=int(cfg.get("l2_top_k", 10)),
            weights=cfg.get("l2_weights") or {},
        )
        self.l3 = BGEReranker(
            top_k=int(cfg.get("l3_top_k", 5)),
            enabled=bool(cfg.get("l3_enabled", False)),
            model_name=str(cfg.get("l3_model", "BAAI/bge-reranker-v2-m3")),
            device=str(cfg.get("l3_device", "cpu")),
            batch_size=int(cfg.get("l3_batch_size", 16)),
            max_length=int(cfg.get("l3_max_length", 512)),
        )
        self.l4 = LLMListwiseReranker(cfg, llm_client)
        self.final_k = int(cfg.get("final_k", 3))
        self.l4_timeout = float(cfg.get("l4_timeout", 3.0))
        # 单工作线程足够：L4 输入只有 Top-5，单次诊断仅提交一个任务
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="rerank-l4")

    def rerank(self, rows: List[Dict[str, Any]], event: Any, now_ms: int) -> RerankResult:
        """执行四层重排，返回 Top-K 案例（case dict，含各层分数与 L4 理由）。"""
        stats = RerankStats(input_count=len(rows or []))
        docs = self.l1.filter(rows_to_documents(rows or []), now_ms)
        stats.l1_out = len(docs)
        stats.dropped_l1 = stats.input_count - len(docs)
        if not docs:
            return RerankResult([], stats)

        query = build_query_text(event)
        current_template = str(
            _event_field(event, "template", "")
            or _event_field(event, "alert_template", "")
            or ""
        )
        docs = self.l2.rerank_documents(
            docs, _event_topology(event), now_ms, current_template=current_template
        )
        stats.l2_out = len(docs)
        if not docs:
            return RerankResult([], stats)

        # L4 只依赖 L2 Top-5：先提交后台线程，与 L3 并行
        l4_input = list(docs[:5])
        l4_started = time.perf_counter()
        l4_future = self._executor.submit(self._run_l4, self.l4, l4_input, query)

        # L3 在当前线程执行（Cross-Encoder 是本地推理，无网络长尾）
        l3_started = time.perf_counter()
        l3_docs = list(self.l3.compress_documents(docs, query=query))
        stats.l3_latency_ms = round((time.perf_counter() - l3_started) * 1000, 2)
        stats.l3_available = bool(l3_docs) and any(
            d.metadata.get("_l3_available") for d in l3_docs
        )
        stats.l3_out = len(l3_docs)

        # join L4：独立超时，不拖垮主流水线 P99
        try:
            l4_future.result(timeout=self.l4_timeout)
            stats.l4_status = "ok" if l4_input and l4_input[0].metadata.get("_l4_available") else "degraded"
        except Exception as exc:  # noqa: BLE001  TimeoutError / 执行异常
            logger.warning("L4 join failed (%s), fallback to L3/L2 fusion", exc)
            l4_future.cancel()
            self.l4.mark_degraded(l4_input)
            stats.l4_status = "degraded"
        stats.l4_latency_ms = round((time.perf_counter() - l4_started) * 1000, 2)

        # 融合基集：L3 输出优先（已按 L2+L3 融合分排序），并入 L4 视野内的其余 L2 Top-5
        seen_ids = {str(d.metadata.get("case_id", "")) for d in l3_docs}
        union = l3_docs + [d for d in l4_input if str(d.metadata.get("case_id", "")) not in seen_ids]
        fuse_final_scores(union)
        union.sort(key=lambda d: d.metadata.get("_final_score", 0.0), reverse=True)
        final = union[: self.final_k]
        stats.final_out = len(final)
        return RerankResult([document_to_case_dict(d) for d in final], stats)

    @staticmethod
    def _run_l4(l4: LLMListwiseReranker, docs: List, query: str) -> List:
        """L4 后台任务：内部已兜底异常，这里仅防未来未捕获异常。"""
        try:
            return l4.rerank(docs, query)
        except Exception as exc:  # noqa: BLE001
            logger.warning("L4 worker crashed, fallback: %s", exc)
            l4.mark_degraded(docs)
            return docs
