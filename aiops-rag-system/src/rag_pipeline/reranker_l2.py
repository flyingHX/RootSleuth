"""L2 多特征融合打分：AIOps 业务知识密度最高的重排层。

七类特征（全部归一化到 [0,1]）：
  f1 语义相似度   Milvus COSINE 召回距离
  f2 拓扑相似度   upstream/downstream/middleware 三层 Jaccard 加权
  f3 时间衰减     exp(-Δt/30d)，超过 180 天额外折扣
  f4 反馈分       贝叶斯平滑（Beta 先验，解决反馈稀疏被刷高问题）
  f5 模板重叠度   Drain 模板 token Jaccard
  f6 案例新鲜度   近 7/30/90 天分桶
  f7 命中频率     召回后人工采纳比例（协同过滤思想）

融合公式（冷启动专家权重，Phase 2 可用 LightGBM LambdaRank 离线学习替换）：
  score = 0.30*f1 + 0.18*f2 + 0.10*f3 + 0.12*f4 + 0.10*f5 + 0.05*f6 + 0.15*f7

实现为 LangChain BaseDocumentCompressor：rerank_documents() 注入业务上下文后
走标准 compress_documents() 接口，可直接挂入 ContextualCompressionRetriever。
"""
import json
import math
from typing import Any, Dict, List, Optional, Sequence

from langchain_core.documents import Document
try:  # langchain-core 0.3.x: 新版在 documents.compressor，旧版在 documents.base
    from langchain_core.documents.compressor import BaseDocumentCompressor
except ImportError:  # pragma: no cover
    from langchain_core.documents.base import BaseDocumentCompressor
from langchain_core.callbacks import Callbacks
from pydantic import Field

_MS_PER_DAY = 86_400_000

DEFAULT_WEIGHTS: Dict[str, float] = {
    "semantic": 0.30,
    "topo": 0.18,
    "time": 0.10,
    "feedback": 0.12,
    "template": 0.10,
    "freshness": 0.05,
    "hitrate": 0.15,
}

# downstream 权重最高：多数故障根因在下游（Redis/MySQL/MQ），上游变更多为连带影响
TOPO_LAYER_WEIGHTS: Dict[str, float] = {"upstream": 0.3, "downstream": 0.5, "middleware": 0.2}


def parse_topology(snapshot: str) -> Dict[str, List[str]]:
    """容错解析 topology_snapshot JSON。"""
    if not snapshot:
        return {}
    try:
        data = json.loads(snapshot)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _jaccard(a: set, b: set) -> float:
    union = a | b
    return len(a & b) / len(union) if union else 0.0


def topo_similarity(current: Dict[str, List[str]], historical: Dict[str, List[str]]) -> float:
    """分层拓扑相似度：三层 Jaccard 加权求和。

    - 双方都缺失该层：中性分 0.5（老案例无 topology_snapshot 不惩罚）；
    - 单边缺失：0.0（信息不对称，惩罚）；
    - 双方都有：Jaccard。
    """
    total = 0.0
    for layer, w in TOPO_LAYER_WEIGHTS.items():
        a = set(current.get(layer, []) or [])
        b = set(historical.get(layer, []) or [])
        if not a and not b:
            sim = 0.5
        elif not a or not b:
            sim = 0.0
        else:
            sim = _jaccard(a, b)
        total += w * sim
    return total


def time_decay(now_ms: int, case_ms: int) -> float:
    """指数时间衰减：30 天半衰期；超过 180 天视为近乎失效（额外 0.3 折扣）。"""
    if not case_ms or not now_ms:
        return 0.0
    days = max(0.0, (now_ms - case_ms) / _MS_PER_DAY)
    return math.exp(-days / 30.0) * (1.0 if days < 180 else 0.3)


def freshness_score(now_ms: int, case_ms: int) -> float:
    """案例新鲜度分桶：近 7 天 1.0 / 30 天 0.7 / 90 天 0.4 / 更旧 0.2。"""
    if not case_ms or not now_ms:
        return 0.2
    days = max(0.0, (now_ms - case_ms) / _MS_PER_DAY)
    if days < 7:
        return 1.0
    if days < 30:
        return 0.7
    if days < 90:
        return 0.4
    return 0.2


def feedback_score(upvotes: int, downvotes: int, prior_mean: float = 0.5, prior_weight: int = 10) -> float:
    """贝叶斯平滑反馈分：样本少时向先验 0.5 收缩，避免单次点赞刷高。"""
    return (upvotes + prior_mean * prior_weight) / (upvotes + downvotes + prior_weight)


def template_overlap(t1: str, t2: str) -> float:
    """Drain 模板 token Jaccard：去掉 <*> 占位符，仅保留字母/数字 token。"""
    def tok(s: str) -> set:
        return {
            w
            for w in str(s or "").lower().replace("<*>", " ").replace("_", " ").split()
            if w.isalnum()
        }

    a, b = tok(t1), tok(t2)
    return _jaccard(a, b)


def hit_rate(hit_count: int, recall_count: int) -> float:
    """命中频率：该案例被召回 Top-3 后人工采纳的比例；样本不足给中性分 0.5。"""
    if recall_count < 5:
        return 0.5
    return max(0.0, min(1.0, hit_count / recall_count))


class BusinessReranker(BaseDocumentCompressor):
    """L2 业务重排器（BaseDocumentCompressor）。

    通过 rerank_documents() 注入拓扑与当前时间（无实例可变状态，并发安全）；
    compress_documents() 使用实例字段 context，供 ContextualCompressionRetriever 场景复用。
    """

    top_k: int = 10
    weights: Dict[str, float] = Field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    context: Dict[str, Any] = Field(default_factory=dict)

    def rerank_documents(
        self,
        docs: List[Document],
        current_topology: Dict[str, List[str]],
        now_ms: int,
        current_template: str = "",
    ) -> List[Document]:
        """业务入口：注入上下文后走标准压缩接口（model_copy 避免并发竞态）。"""
        scoped = self.model_copy(
            update={
                "context": {
                    "topology": current_topology or {},
                    "now_ms": now_ms,
                    "template": current_template or "",
                }
            }
        )
        return list(scoped.compress_documents(docs, query=""))

    def compress_documents(
        self,
        documents: Sequence[Document],
        query: str,
        callbacks: Optional[Callbacks] = None,
    ) -> Sequence[Document]:
        """七特征融合打分、排序并截断到 top_k。"""
        ctx = self.context or {}
        current_topology = ctx.get("topology", {}) or {}
        now_ms = int(ctx.get("now_ms", 0) or 0)
        w = {**DEFAULT_WEIGHTS, **(self.weights or {})}

        for d in documents:
            md = d.metadata
            f1 = float(md.get("distance", 0.5) or 0.0)
            historical_topo = parse_topology(str(md.get("topology_snapshot", "") or ""))
            f2 = topo_similarity(current_topology, historical_topo)
            f3 = time_decay(now_ms, int(md.get("start_time", 0) or 0))
            f4 = self._feedback_feature(md)
            f5 = template_overlap(str(md.get("alert_template", "") or ""), str(ctx.get("template", "") or ""))
            f6 = freshness_score(now_ms, int(md.get("start_time", 0) or 0))
            f7 = hit_rate(int(md.get("hit_count", 0) or 0), int(md.get("recall_count", 0) or 0))

            score = (
                w["semantic"] * f1
                + w["topo"] * f2
                + w["time"] * f3
                + w["feedback"] * f4
                + w["template"] * f5
                + w["freshness"] * f6
                + w["hitrate"] * f7
            )
            md["_f1_semantic"] = round(f1, 4)
            md["_f2_topo"] = round(f2, 4)
            md["_f3_time"] = round(f3, 4)
            md["_f4_feedback"] = round(f4, 4)
            md["_f5_template"] = round(f5, 4)
            md["_f6_freshness"] = round(f6, 4)
            md["_f7_hitrate"] = round(f7, 4)
            md["_l2_score"] = round(score, 4)

        # 稳定排序（波动治理）：同分候选按 case_id 升序 tie-break，跨次运行顺序一致
        ranked = sorted(
            documents,
            key=lambda d: (
                -float(d.metadata.get("_l2_score", 0.0) or 0.0),
                str(d.metadata.get("case_id", "")),
            ),
        )
        return ranked[: self.top_k] if self.top_k and self.top_k > 0 else ranked

    @staticmethod
    def _feedback_feature(md: Dict[str, Any]) -> float:
        """f4：新 Schema 走贝叶斯平滑；旧数据（仅 feedback_score）回退线性映射。"""
        up = int(md.get("upvotes", 0) or 0)
        down = int(md.get("downvotes", 0) or 0)
        if up or down:
            return feedback_score(up, down)
        legacy = int(md.get("feedback_score", 0) or 0)
        if legacy == 0:
            return 0.5
        return min(1.0, max(0.0, (legacy + 10) / 20.0))
