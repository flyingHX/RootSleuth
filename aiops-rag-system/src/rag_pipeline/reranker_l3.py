"""L3 Cross-Encoder 精排：BGE-Reranker 对 (query, doc) 逐对交叉注意力打分。

与 L2 特征工程（Bi-Encoder 范式）互补：Cross-Encoder 捕捉 query-doc 细粒度语义交互。

可用性策略（保证整链无 FlagEmbedding/GPU 时依然可用）：
- enabled=False（默认）：直接透传，不加载模型；
- enabled=True 但 FlagEmbedding 缺失或模型加载失败：标记 _l3_available=False，
  原样返回输入（保持 L2 顺序），编排层据此回退融合权重。

融合保底：_fused_score = 0.3 * L2 + 0.7 * L3。
"""
from typing import Any, ClassVar, Dict, List, Optional, Sequence

from langchain_core.documents import Document
try:  # langchain-core 0.3.x: 新版在 documents.compressor，旧版在 documents.base
    from langchain_core.documents.compressor import BaseDocumentCompressor
except ImportError:  # pragma: no cover
    from langchain_core.documents.base import BaseDocumentCompressor
from langchain_core.callbacks import Callbacks
from pydantic import Field

from ..utils.logger import get_logger

logger = get_logger(__name__)

_L2_WEIGHT = 0.3
_L3_WEIGHT = 0.7


class BGEReranker(BaseDocumentCompressor):
    """BGE-Reranker Cross-Encoder 压缩器（懒加载 + 失败降级）。"""

    model_name: str = "BAAI/bge-reranker-v2-m3"
    device: str = "cpu"
    top_k: int = 5
    batch_size: int = 16
    max_length: int = 512
    enabled: bool = False

    # 进程级模型缓存：同模型多实例共享；加载失败标记避免重复尝试拖慢 P99
    _MODELS: ClassVar[Dict[str, Any]] = {}
    _FAILED: ClassVar[set] = set()

    l2_fusion_weight: float = Field(default=_L2_WEIGHT)
    l3_fusion_weight: float = Field(default=_L3_WEIGHT)

    def is_available(self) -> bool:
        """探测模型可用性（enabled 且加载成功）。"""
        return self.enabled and self._get_model() is not None

    def _get_model(self) -> Optional[Any]:
        if not self.enabled:
            return None
        if self.model_name in self._FAILED:
            return None
        model = self._MODELS.get(self.model_name)
        if model is not None:
            return model
        try:
            from FlagEmbedding import FlagReranker  # 延迟导入：torch 依赖不强制

            model = FlagReranker(self.model_name, use_fp16=(self.device == "cuda"))
            self._MODELS[self.model_name] = model
            logger.info("BGE-Reranker loaded: %s (device=%s)", self.model_name, self.device)
            return model
        except Exception as exc:  # noqa: BLE001
            self._FAILED.add(self.model_name)
            logger.warning("BGE-Reranker unavailable (%s): %s", self.model_name, exc)
            return None

    def compress_documents(
        self,
        documents: Sequence[Document],
        query: str,
        callbacks: Optional[Callbacks] = None,
    ) -> Sequence[Document]:
        """对 (query, doc) 逐对打分；任何失败均降级为原样透传。"""
        if not documents:
            return []
        model = self._get_model()
        if model is None or not query:
            self._mark_unavailable(documents)
            return list(documents)

        try:
            pairs: List[List[str]] = [[query, d.page_content] for d in documents]
            scores = model.compute_score(
                pairs,
                normalize=True,
                batch_size=self.batch_size,
                max_length=self.max_length,
            )
            if isinstance(scores, (int, float)):
                scores = [scores]
            scored = [
                (d, float(s))
                for d, s in zip(documents, scores)
            ]
        except Exception as exc:  # noqa: BLE001
            logger.warning("L3 cross-encoder scoring failed, passthrough: %s", exc)
            self._mark_unavailable(documents)
            return list(documents)

        for d, s in scored:
            d.metadata["_l3_score"] = round(s, 4)
            d.metadata["_l3_available"] = True
            # 与 L2 分数融合（保底：L2 占 30%，L3 占 70%）
            d.metadata["_fused_score"] = round(
                self.l2_fusion_weight * float(d.metadata.get("_l2_score", 0.0))
                + self.l3_fusion_weight * s,
                4,
            )
        ranked = sorted(scored, key=lambda p: p[0].metadata.get("_fused_score", 0.0), reverse=True)
        ranked_docs = [d for d, _ in ranked]
        return ranked_docs[: self.top_k] if self.top_k and self.top_k > 0 else ranked_docs

    @staticmethod
    def _mark_unavailable(documents: Sequence[Document]) -> None:
        for d in documents:
            d.metadata["_l3_available"] = False
