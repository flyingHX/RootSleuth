"""RAG 检索流水线核心：四步走（向量构建 -> 粗筛 -> ANN 精筛 -> 业务重排）+ LLM 推理。"""
import json
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Dict, List, Union

from ..models.event import StandardizedEvent
from ..utils.logger import get_logger
from ..utils.metrics import rag_latency, rag_search_total, rag_stage_latency
from .embedder import BGEEmbedder
from .llm_client import LLMClient
from .milvus_client import MilvusClient
from .prompt_builder import build_prompt
from .reranker import Reranker

logger = get_logger(__name__)


class RAGPipeline:
    """完整的 RAG 检索推理引擎，目标检索 P99 < 500ms、LLM P99 < 3s。

    P99 长尾治理：
    - 各阶段（embedding/milvus_search/rerank/llm）独立计时并暴露
      rag_stage_latency_seconds 指标，支持线上分阶段长尾定位。
    - LLM 底层重试默认关闭、线程池大小可配置；超时或异常均快速降级 top1，
      不再丢弃已就绪的检索结果等待完整 LLM timeout。
    - search() 兼容 dict 事件（Kafka 消费路径），修复属性访问缺陷。
    """

    def __init__(self, config: dict):
        self.milvus = MilvusClient(config.get("milvus", {}))
        self.embedder = BGEEmbedder(config.get("embedder", {}))
        self.reranker = Reranker()
        self.llm = LLMClient(config.get("llm", {}))
        self.top_k = int(config.get("top_k", 20))
        self.final_k = int(config.get("final_k", 3))
        self.timeout_seconds = float(config.get("llm_timeout", 5.0))
        self.llm_max_workers = int(config.get("llm_max_workers", 8))
        self.recent_window_days = int(config.get("recent_window_days", 30))
        self.executor = ThreadPoolExecutor(
            max_workers=self.llm_max_workers, thread_name_prefix="rag-llm"
        )

    def search(self, event: Union[StandardizedEvent, Dict]) -> Dict:
        """完整的 RAG 检索流水线入口（兼容 dict 事件，Kafka 消费路径直接可用）。"""
        start_time = time.perf_counter()
        try:
            if isinstance(event, dict):
                event = StandardizedEvent(**event)

            # Step 1: 生成查询向量
            query_text = self._build_query_text(event)
            stage = time.perf_counter()
            query_vector = self.embedder.embed(query_text)
            rag_stage_latency.labels(stage="embedding").observe(time.perf_counter() - stage)

            # Step 2 + 3: 双路召回（结构化粗筛 + ANN 精筛）
            expr = self._build_filter_expr(event)
            partitions = self.milvus.recent_partitions(3)
            stage = time.perf_counter()
            search_results = self.milvus.search(
                query_vector=query_vector,
                expr=expr,
                partition_names=partitions,
                limit=self.top_k,
                output_fields=[
                    "case_id", "root_cause", "solution", "start_time",
                    "topology_snapshot", "feedback_score", "alert_template",
                ],
            )
            rag_stage_latency.labels(stage="milvus_search").observe(time.perf_counter() - stage)

            if not search_results:
                rag_search_total.labels(status="no_result").inc()
                return self._finalize(self._fallback_no_result(event), start_time, [])

            # Step 4: 业务重排
            stage = time.perf_counter()
            reranked = self.reranker.rerank(
                candidates=search_results,
                current_topology=event.topology,
                current_time=event.timestamp,
            )
            rag_stage_latency.labels(stage="rerank").observe(time.perf_counter() - stage)
            top_cases = reranked[: self.final_k]

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

            return self._finalize(result, start_time, top_cases)
        except Exception as exc:  # noqa: BLE001
            logger.error("RAG pipeline error: %s", exc)
            rag_search_total.labels(status="error").inc()
            return self._finalize(
                self._fallback_no_result(event, reason=str(exc)), start_time, []
            )
        finally:
            rag_latency.observe(time.perf_counter() - start_time)

    # ---------- 内部步骤 ----------
    @staticmethod
    def _build_query_text(event: StandardizedEvent) -> str:
        return (
            f"【故障类型】: {event.error_type}\n"
            f"【影响服务】: {event.service_name}\n"
            f"【告警特征】: {event.template}\n"
            f"【下游依赖】: {event.topology.get('downstream', [])}"
        )

    def _build_filter_expr(self, event: StandardizedEvent) -> str:
        since = event.timestamp - self.recent_window_days * 24 * 3600 * 1000
        return (
            f'service_name == "{event.service_name}" and '
            f'error_type == "{event.error_type}" and '
            f"start_time > {since}"
        )

    @staticmethod
    def _finalize(result: Dict, start_time: float, top_cases: List[Dict]) -> Dict:
        """补齐端到端耗时与相似案例列表（供诊断接口直接返回）。"""
        result["latency_ms"] = int((time.perf_counter() - start_time) * 1000)
        result["similar_cases"] = [
            {
                "case_id": str(c.get("case_id", "") or ""),
                "similarity": float(c.get("distance", 0.0) or 0.0),
                "final_score": float(c.get("_final_score", 0.0) or 0.0),
                "alert_template": str(c.get("alert_template", "") or ""),
                "root_cause": str(c.get("root_cause", "") or ""),
                "solution": str(c.get("solution", "") or ""),
                "start_time": int(c.get("start_time", 0) or 0),
            }
            for c in top_cases
        ]
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
        embedding = self.embedder.embed(
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
