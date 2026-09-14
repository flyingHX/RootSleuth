"""知识案例模型（Milvus aiops_knowledge_base 集合的字段映射）。"""
from pydantic import BaseModel, Field
from typing import Optional, Dict, List


class KnowledgeCase(BaseModel):
    case_id: str
    fingerprint: str = ""
    service_name: str = ""
    cluster: str = ""
    error_type: str = ""
    severity: int = 2
    start_time: int = 0
    feedback_score: int = 0
    root_cause: str = ""
    solution: str = ""
    alert_template: str = ""
    topology_snapshot: str = '{"upstream":[],"downstream":[]}'
    resolved_by: str = "human"
    embedding: Optional[List[float]] = None
    created_at: int = 0

    def topology_dict(self) -> Dict[str, List[str]]:
        import json
        try:
            return json.loads(self.topology_snapshot)
        except (json.JSONDecodeError, TypeError):
            return {"upstream": [], "downstream": []}

    def to_milvus_row(self) -> dict:
        return {
            "case_id": self.case_id,
            "fingerprint": self.fingerprint,
            "service_name": self.service_name,
            "cluster": self.cluster,
            "error_type": self.error_type,
            "severity": self.severity,
            "start_time": self.start_time,
            "feedback_score": self.feedback_score,
            "root_cause": self.root_cause[:2048],
            "solution": self.solution[:2048],
            "alert_template": self.alert_template[:1024],
            "topology_snapshot": self.topology_snapshot[:1024],
            "resolved_by": self.resolved_by,
            "embedding": self.embedding,
            "created_at": self.created_at,
        }
