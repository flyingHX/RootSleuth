"""RAG 检索流水线核心（LangChain 化）：

召回链（LangChain BaseRetriever）：
  Embedding（BGEEmbedder）-> Milvus 双路召回 Top-20（MilvusEventRetriever）
四层业务重排（RerankPipeline）：
  L1 规则硬过滤 -> L2 七特征融合 -> (L3 Cross-Encoder ∥ L4 LLM Listwise) -> Score Fusion -> Top-3
诊断推理：
  Few-shot Prompt（prompt_builder）-> LLM（LLMClient，超时/异常熔断快速降级 top1）

P99 长尾治理：
- 各阶段独立计时：rag_stage_latency_seconds（embedding/milvus_search/rerank/llm）
  + rerank_layer_latency_seconds（l1/l2/l3/l4/fusion 分层）。
- 每层重排独立降级（LLM 挂走 L3、Cross-Encoder 挂走 L2），L4 后台线程 + 独立超时。
- LLM 底层重试默认关闭、线程池大小可配置；超时或异常均快速降级 top1。
- search() 兼容 dict 事件（Kafka 消费路径）。
"""
import json
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Dict, List, Union

from ..models.event import StandardizedEvent
from ..utils.logger import get_logger
from ..utils.metrics import (
    rag_latency,
    rag_search_total,
    rag_stage_latency,
    rerank_degraded_total,
    rerank_final_total,
    rerank_layer_latency,
)
from .embedder import BGEEmbedder
from .llm_client import LLMClient
from .milvus_client import MilvusClient
from .prompt_builder import build_prompt
from .retriever import MilvusEventRetriever
from .rerank_pipeline import RerankPipeline

logger = get_logger(__name__)

# 召回字段 -> 输出字段（含四层重排所需的反馈/召回统计与新 Schema 字段）
_OUTPUT_FIELDS = [
    "case_id", "fingerprint", "service_name", "cluster", "error_type",
    "severity", "start_time", "feedback_score", "upvotes", "downvotes",
    "hit_count", "recall_count", "root_cause", "solution", "alert_template",
    "topology_snapshot", "resolved_by", "created_at",
]


class RAGPipeline:
    """完整的 RAG 检索推理引擎，目标检索 P99 < 500ms、LLM P99 < 3s。"""

    def __init__(self, config: dict):
        self.milvus = MilvusClient(config.get("milvus", {}))
        self.embedder = BGEEmbedder(config.get("embedder", {}))
        self.llm = LLMClient(config.get("llm", {}))
        self.top_k = int(config.get("top_k", 20))
        self.final_k = int(config.get("final_k", 3))
        self.timeout_seconds = float(config.get("llm_timeout", 5.0))
        self.llm_max_workers = int(config.get("llm_max_workers", 8))
        self.recent_window_days = int(config.get("recent_window_days", 30))
        # LangChain 检索链（双路召回包装为 BaseRetriever）
        self.retriever = MilvusEventRetriever(
            milvus_client=self.milvus,
            embedder=self.embedder,
            top_k=self.top_k,
            recent_window_days=self.recent_window_days,
        )
        # 四层业务重排编排（L1 -> L2 -> (L3 ∥ L4) -> Fusion）
        self.rerank_pipeline = RerankPipeline(config, llm_client=self.llm)
        # 兼容旧属性名（测试/外部脚本引用 pipeline.llm.invoke / pipeline.milvus）
        self.executor = ThreadPoolExecutor(
            max_workers=self.llm_max_workers, thread_name_prefix="rag-llm"
        )

    def search(self, event: Union[StandardizedEvent, Dict]) -> Dict:
        """完整的 RAG 检索流水线入口（兼容 dict 事件，Kafka 消费路径直接可用）。"""
        start_time = time.perf_counter()
        try:
            if isinstance(event, dict):
                event = StandardizedEvent(**event)

            # Step 1: 生成查询向量（经 LangChain Embedder 适配器）
            stage = time.perf_counter()
            query_vector = self.retriever.embed_query_text(self._build_query_text(event))
            rag_stage_latency.labels(stage="embedding").observe(time.perf_counter() - stage)

            # Step 2 + 3: LangChain Retriever 双路召回（结构化粗筛 + ANN 精筛）
            stage = time.perf_counter()
            candidate_docs = self.retriever.retrieve_event(event, query_vector)
            rag_stage_latency.labels(stage="milvus_search").observe(time.perf_counter() - stage)

            if not candidate_docs:
                rag_search_total.labels(status="no_result").inc()
                return self._finalize(self._fallback_no_result(event), start_time, [])

            # Step 4: 四层业务重排（L1 -> L2 -> (L3 ∥ L4) -> Fusion -> Top-3）
            stage = time.perf_counter()
            rerank_result = self.rerank_pipeline.rerank(
                rows=candidate_docs, event=event, now_ms=event.timestamp
            )
            rerank_elapsed = time.perf_counter() - stage
            rag_stage_latency.labels(stage="rerank").observe(rerank_elapsed)
            top_cases = rerank_result.cases
            self._record_rerank_metrics(rerank_result.stats)

            # 构建 Few-shot Prompt 并调用 LLM（超时/异常熔断，快速降级 top1）
            prompt = build_prompt(event, top_cases)
            stage = time.perf_counter()
            future = self.executor.submit(self.llm.invoke, prompt)
            try:
                llm_result = future.result(timeout=self.timeout_seconds)
                result = self._parse_llm_result(llm_result, top_cases, event)
                rag_search_total.labels(status="llm_ok").inc()
            except FutureTimeoutError:
                future.cancel()
                result = self._fallback_top1(event, top_cases, reason="LLM timeout")
                rag_search_total.labels(status="llm_timeout").inc()
            except Exception as exc:  # noqa: BLE001
                # LLM 调用异常（网络/限流/鉴权）不应丢弃已就绪的检索结果
                future.cancel()
                logger.warning("LLM invoke failed, fallback to top1: %s", exc)
                result = self._fallback_top1(event, top_cases, reason=f"LLM error: {exc}")
                rag_search_total.labels(status="llm_error").inc()
            rag_stage_latency.labels(stage="llm").observe(time.perf_counter() - stage)

            # 知识库召回统计回流：Top-3 进入 LLM 上下文即 recall_count+1
            self._record_recall_stats(top_cases)

            return self._finalize(result, start_time, top_cases, rerank_result.stats)
        except Exception as exc:  # noqa: BLE001
            logger.error("RAG pipeline error: %s", exc)
            rag_search_total.labels(status="error").inc()
            return self._finalize(
                self._fallback_no_result(event, reason=str(exc)), start_time, []
            )
        finally:
            rag_latency.observe(time.perf_counter() - start_time)

    def retrieve_top_cases(self, event: Union[StandardizedEvent, Dict], top_k: int = 5) -> List[Dict]:
        """仅召回 + 四层重排（不调用诊断 LLM）：供控制台诊断 Agent 的 search_kb 工具使用。

        与 search() 的差异：跳过 LLM 诊断与召回统计回流，返回带各层分数的 Top-K 案例；
        宽松构造事件（缺失字段给中性默认值），任何异常静默降级为空列表（fail-open）。
        """
        try:
            if isinstance(event, dict):
                event = StandardizedEvent(
                    **{
                        "event_id": "adhoc", "fingerprint": "", "service_name": "",
                        "cluster": "", "error_type": "",
                        **{k: v for k, v in event.items() if v is not None},
                    }
                )
            query_vector = self.retriever.embed_query_text(self._build_query_text(event))
            candidate_docs = self.retriever.retrieve_event(event, query_vector)
            if not candidate_docs:
                return []
            # 缺省时间戳回退为当前毫秒，避免 L1 过期过滤 / L2 时间衰减在 now_ms=0 下误杀
            now_ms = event.timestamp if event.timestamp > 0 else int(time.time() * 1000)
            rerank_result = self.rerank_pipeline.rerank(
                rows=candidate_docs, event=event, now_ms=now_ms
            )
            return [
                {
                    "case_id": str(c.get("case_id", "") or ""),
                    "service_name": str(c.get("service_name", "") or ""),
                    "error_type": str(c.get("error_type", "") or ""),
                    "alert_template": str(c.get("alert_template", "") or ""),
                    "root_cause": str(c.get("root_cause", "") or ""),
                    "solution": str(c.get("solution", "") or ""),
                    "score": float(c.get("_final_score", 0.0) or 0.0),
                    "l2_score": float(c["_l2_score"]) if c.get("_l2_score") is not None else None,
                    "l3_score": float(c["_l3_score"]) if c.get("_l3_score") is not None else None,
                    "l4_score": float(c["_l4_score"]) if c.get("_l4_score") is not None else None,
                }
                for c in rerank_result.cases[: max(1, top_k)]
            ]
        except Exception as exc:  # noqa: BLE001 - fail-open：召回异常返回空列表
            logger.warning("retrieve_top_cases failed: %s", exc)
            return []

    # ---------- 内部步骤 ----------
    @staticmethod
    def _build_query_text(event: StandardizedEvent) -> str:
        return (
            f"【故障类型】: {event.error_type}\n"
            f"【影响服务】: {event.service_name}\n"
            f"【告警特征】: {event.template}\n"
            f"【下游依赖】: {event.topology.get('downstream', [])}"
        )

    def _record_rerank_metrics(self, stats) -> None:
        """四层重排分层指标：逐层延迟、降级事件与最终漏斗产出规模。"""
        for layer, seconds in (
            ("l3", stats.l3_latency_ms / 1000.0),
            ("l4", stats.l4_latency_ms / 1000.0),
        ):
            if seconds > 0:
                rerank_layer_latency.labels(layer=layer).observe(seconds)
        if stats.l1_out < stats.input_count:
            rerank_layer_latency.labels(layer="l1").observe(0.001)
        if not stats.l3_available and stats.l3_latency_ms > 0:
            rerank_degraded_total.labels(layer="l3").inc()
        if stats.l4_status == "degraded":
            rerank_degraded_total.labels(layer="l4").inc()
        rerank_final_total.labels(size=str(stats.final_out)).inc()

    def _record_recall_stats(self, top_cases: List[Dict]) -> None:
        """召回统计异步回流（f7 命中频率特征的数据基础），失败静默。"""
        case_ids = [str(c.get("case_id", "")) for c in top_cases if c.get("case_id")]
        if not case_ids:
            return
        self.executor.submit(self.milvus.update_recall_stats, case_ids, None)

    def _finalize(
        self,
        result: Dict,
        start_time: float,
        top_cases: List[Dict],
        stats=None,
    ) -> Dict:
        """补齐端到端耗时、相似案例列表与四层重排统计（供诊断接口直接返回）。"""
        result["latency_ms"] = int((time.perf_counter() - start_time) * 1000)
        result["similar_cases"] = [
            {
                "case_id": str(c.get("case_id", "") or ""),
                "similarity": float(c.get("distance", 0.0) or 0.0),
                "final_score": float(c.get("_final_score", 0.0) or 0.0),
                "l2_score": (
                    float(c["_l2_score"]) if c.get("_l2_score") is not None else None
                ),
                "l3_score": (
                    float(c["_l3_score"]) if c.get("_l3_score") is not None else None
                ),
                "l4_score": (
                    float(c["_l4_score"]) if c.get("_l4_score") is not None else None
                ),
                "rerank_reason": str(c.get("_l4_reason", "") or "") or None,
                "alert_template": str(c.get("alert_template", "") or ""),
                "root_cause": str(c.get("root_cause", "") or ""),
                "solution": str(c.get("solution", "") or ""),
                "start_time": int(c.get("start_time", 0) or 0),
            }
            for c in top_cases
        ]
        if stats is not None:
            result["rerank_stats"] = {
                "input_count": stats.input_count,
                "l1_out": stats.l1_out,
                "l2_out": stats.l2_out,
                "l3_out": stats.l3_out,
                "final_out": stats.final_out,
                "l3_available": stats.l3_available,
                "l4_status": stats.l4_status,
            }
        return result

    def _parse_llm_result(self, llm_output: str, top_cases: List[Dict], event: StandardizedEvent) -> Dict:
        parsed = self.llm.parse_json(llm_output)
        if parsed is None:
            return self._fallback_top1(event, top_cases, reason="LLM output parse failed")

        actions = parsed.get("suggest_actions") or [top_cases[0].get("solution", "")]
        return {
            "event_id": event.event_id,
            "root_cause": parsed.get("root_cause_service", top_cases[0].get("root_cause", "")),
            "solution": actions[0] if actions else top_cases[0].get("solution", ""),
            "confidence": float(parsed.get("confidence", 0.7)),
            "suggest_actions": list(actions),
            "is_fallback": False,
            "reason": None,
        }

    @staticmethod
    def _fallback_top1(event: StandardizedEvent, top_cases: List[Dict], reason: str) -> Dict:
        top = top_cases[0]
        return {
            "event_id": event.event_id,
            "root_cause": top.get("root_cause", "未知"),
            "solution": top.get("solution", "请人工介入"),
            "confidence": 0.5,
            "suggest_actions": [top.get("solution", "")],
            "is_fallback": True,
            "reason": reason,
        }

    @staticmethod
    def _fallback_no_result(event, reason: str = "No similar cases found") -> Dict:
        event_id = (
            event.get("event_id", "unknown") if isinstance(event, dict) else event.event_id
        )
        return {
            "event_id": event_id,
            "root_cause": "未找到相似历史案例，请人工排查",
            "solution": "建议检查服务日志和监控指标",
            "confidence": 0.0,
            "suggest_actions": ["人工介入"],
            "is_fallback": True,
            "reason": reason,
        }

    # ---------- 知识库写入（自进化闭环）----------
    def write_case(
        self,
        event: StandardizedEvent,
        root_cause: str,
        solution: str,
        resolved_by: str = "human",
    ) -> str:
        """告警闭环后写入/覆盖知识案例：按 fingerprint 去重。"""
        from ..models.case import KnowledgeCase

        existing = self.milvus.query_by_fingerprint(event.fingerprint)
        case_id = (
            existing[0]["case_id"]
            if existing
            else f"case_{time.strftime('%Y%m%d_%H%M%S')}"
        )
        embedding = self.retriever.embed_query_text(
            f"{event.template} {event.service_name} {event.error_type} {root_cause} {solution}"
        )
        case = KnowledgeCase(
            case_id=case_id,
            fingerprint=event.fingerprint,
            service_name=event.service_name,
            cluster=event.cluster,
            error_type=event.error_type,
            severity=event.severity,
            start_time=event.timestamp,
            root_cause=root_cause[:2048],
            solution=solution[:2048],
            alert_template=event.template[:1024],
            topology_snapshot=json.dumps(event.topology, ensure_ascii=False)[:1024],
            resolved_by=resolved_by,
            embedding=embedding,
            created_at=int(time.time() * 1000),
        )
        ok = self.milvus.upsert_case(case.to_milvus_row())
        logger.info("Write case %s (ok=%s, dedup=%s)", case_id, ok, bool(existing))
        return case_id

    def upsert_console_case(self, fields: dict) -> str:
        """控制台知识治理同步入口：按 case_id 直接 upsert 知识案例（无需 StandardizedEvent）。

        与 write_case 的差异：write_case 以"事件关闭"为源头（fingerprint 去重），本方法以
        "审批发布后的案例"为源头（case_id 为主键幂等），供发布/更新/回滚索引同步闭环使用。
        Milvus 写入失败抛 RuntimeError（由 kb-sync API 转换为 503，控制台降级留痕）。
        """
        from ..models.case import KnowledgeCase

        text_parts = [
            fields.get("alert_template") or "",
            fields.get("service_name") or "",
            fields.get("error_type") or "",
            fields.get("root_cause") or "",
            fields.get("solution") or "",
        ]
        embedding = self.retriever.embed_query_text(" ".join(part for part in text_parts if part))
        created_at = int(fields.get("created_at") or time.time() * 1000)
        case = KnowledgeCase(
            case_id=str(fields.get("case_id") or f"case_{time.strftime('%Y%m%d_%H%M%S')}"),
            fingerprint=str(fields.get("fingerprint") or ""),
            service_name=str(fields.get("service_name") or ""),
            cluster=str(fields.get("cluster") or ""),
            error_type=str(fields.get("error_type") or ""),
            severity=int(fields.get("severity") or 2),
            start_time=created_at,
            feedback_score=int(fields.get("feedback_score") or 0),
            upvotes=int(fields.get("upvotes") or 0),
            downvotes=int(fields.get("downvotes") or 0),
            hit_count=int(fields.get("hit_count") or 0),
            recall_count=int(fields.get("recall_count") or 0),
            root_cause=str(fields.get("root_cause") or "")[:2048],
            solution=str(fields.get("solution") or "")[:2048],
            alert_template=str(fields.get("alert_template") or "")[:1024],
            topology_snapshot=(
                str(fields.get("topology_snapshot")) if fields.get("topology_snapshot") else '{"upstream":[],"downstream":[]}'
            )[:1024],
            resolved_by=str(fields.get("resolved_by") or "console"),
            kb_version=int(fields.get("kb_version") or 0),
            embedding=embedding,
            created_at=created_at,
        )
        if not self.milvus.upsert_case(case.to_milvus_row()):
            raise RuntimeError("milvus_upsert_failed")
        logger.info("Console sync upserted case %s", case.case_id)
        return case.case_id

    def set_cache_epoch_source(self, redis_client) -> None:
        """注入 Redis 缓存 epoch 源（多实例语义缓存失效对账）；测试环境不注入则跳过对账。"""
        self.embedder.set_epoch_source(redis_client)

    def invalidate_case_cache(self, case_id: str = "") -> int:
        """知识变更（发布/更新/回滚/归档/删除）后失效语义缓存，返回清空的缓存条目数。

        与 kb_cache API、Redis Pub/Sub 广播订阅方共用同一入口，保证单实例与
        多实例部署下的缓存一致性语义收敛在同一段实现上。
        """
        return self.embedder.invalidate(case_id=case_id)
