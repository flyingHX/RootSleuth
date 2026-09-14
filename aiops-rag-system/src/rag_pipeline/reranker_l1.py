"""L1 规则硬过滤：在打分前剔除明确不该出现的案例（最快、最省算力的一层）。

命中任一条件直接剔除：
1. 案例过旧（start_time 距今超过 max_age_days）；
2. 案例黑名单 / 服务黑名单；
3. 差评过多（净反馈分低于 min_feedback）；
4. 方案含废弃关键词（如已下线组件、旧版本命令）。

产出示例：20 -> ~15 条，耗时 < 1ms。本层不依赖任何外部服务，永不确定失败。
"""
from typing import Any, Dict, List

from langchain_core.documents import Document

from ..utils.logger import get_logger

logger = get_logger(__name__)

_MS_PER_DAY = 86_400_000


class RuleFilter:
    """规则硬过滤器（L1）。配置键与 config["rerank"] 对齐。"""

    def __init__(self, cfg: Dict[str, Any]):
        self.max_age_days = float(cfg.get("l1_max_age_days", 365))
        self.blacklist_case_ids = set(cfg.get("l1_blacklist_case_ids") or [])
        self.blacklist_services = set(cfg.get("l1_blacklist_services") or [])
        self.min_feedback = int(cfg.get("l1_min_feedback", -5))
        self.deprecated_keywords = list(cfg.get("l1_deprecated_keywords") or [])

    def filter(self, docs: List[Document], now_ms: int) -> List[Document]:
        """返回通过全部硬规则的文档子集（保持原顺序）。"""
        out: List[Document] = []
        for d in docs:
            md = d.metadata
            # 1. 案例过旧
            start_time = int(md.get("start_time", 0) or 0)
            age_days = (now_ms - start_time) / _MS_PER_DAY if start_time else 0.0
            if start_time and age_days > self.max_age_days:
                continue
            # 2. 黑名单（案例 / 服务）
            if str(md.get("case_id", "")) in self.blacklist_case_ids:
                continue
            if str(md.get("service_name", "")) in self.blacklist_services:
                continue
            # 3. 差评过多（净反馈分：upvotes-downvotes，旧数据回退 feedback_score）
            if self._net_feedback(md) < self.min_feedback:
                continue
            # 4. 方案含废弃关键词
            solution = str(md.get("solution", "") or "")
            if any(kw in solution for kw in self.deprecated_keywords):
                continue
            out.append(d)
        filtered = len(docs) - len(out)
        if filtered:
            logger.debug("L1 rule filter: %d -> %d (dropped %d)", len(docs), len(out), filtered)
        return out

    @staticmethod
    def _net_feedback(md: Dict[str, Any]) -> int:
        """净反馈分：新 Schema 用 upvotes-downvotes，旧数据回退 feedback_score。"""
        up = int(md.get("upvotes", 0) or 0)
        down = int(md.get("downvotes", 0) or 0)
        if up or down:
            return up - down
        return int(md.get("feedback_score", 0) or 0)
