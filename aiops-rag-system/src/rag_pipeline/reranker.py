"""业务重排：余弦相似度 + 拓扑相似度 + 时间衰减 + 反馈加权，取 Top K。"""
import json
import math
from typing import Dict, List


class Reranker:
    """Final_Score = 0.6*余弦 + 0.2*拓扑 + 0.1*时间衰减 + 0.1*反馈"""

    def __init__(self, weights: Dict[str, float] = None):
        self.weights = weights or {
            "cosine": 0.6,
            "topology": 0.2,
            "time": 0.1,
            "feedback": 0.1,
        }

    def rerank(
        self, candidates: List[Dict], current_topology: Dict, current_time: int
    ) -> List[Dict]:
        for case in candidates:
            cos_sim = float(case.get("distance", 0.5))
            topo_sim = self._calc_topo_similarity(
                current_topology.get("downstream", []),
                self._parse_topology(case.get("topology_snapshot", "")).get("downstream", []),
            )
            time_diff_days = max(
                0.0, (current_time - int(case.get("start_time", current_time))) / (1000 * 3600 * 24)
            )
            time_decay = math.exp(-time_diff_days / 30.0)
            feedback = min(1.0, max(0.0, (int(case.get("feedback_score", 0)) + 10) / 20.0))

            w = self.weights
            case["_final_score"] = round(
                w["cosine"] * cos_sim
                + w["topology"] * topo_sim
                + w["time"] * time_decay
                + w["feedback"] * feedback,
                4,
            )

        candidates.sort(key=lambda x: x.get("_final_score", 0), reverse=True)
        return candidates

    @staticmethod
    def _calc_topo_similarity(current: List[str], historical: List[str]) -> float:
        if not current or not historical:
            return 0.5
        set_c, set_h = set(current), set(historical)
        union = len(set_c | set_h)
        return len(set_c & set_h) / union if union > 0 else 0.0

    @staticmethod
    def _parse_topology(snapshot: str) -> Dict:
        try:
            return json.loads(snapshot) if snapshot else {"downstream": []}
        except json.JSONDecodeError:
            return {"downstream": []}
