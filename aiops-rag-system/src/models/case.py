"""知识案例模型（Milvus aiops_knowledge_base 集合的字段映射）。"""
from pydantic import BaseModel
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
    # 反馈闭环拆分字段：f4 贝叶斯平滑（upvotes/downvotes）、f7 命中频率（hit/recall）的数据基础
    upvotes: int = 0
    downvotes: int = 0
    hit_count: int = 0
    recall_count: int = 0
    root_cause: str = ""
    solution: str = ""
    alert_template: str = ""
    topology_snapshot: str = '{"upstream":[],"downstream":[]}'
    resolved_by: str = "human"
    # 知识版本号（发布/更新/回滚时递增；RAG 侧用于拒绝旧版本覆盖的版本守卫）
    kb_version: int = 0
    # 租户归属（P0-2 多租户隔离；default 为公共知识层）
    tenant_id: str = "default"
    # 部署环境（P0-2 环境隔离；prod/staging/...，空 = 公共/不过滤）
    environment: str = ""
    embedding: Optional[List[float]] = None
    created_at: int = 0

    def topology_dict(self) -> Dict[str, List[str]]:
        import json

        try:
            data = json.loads(self.topology_snapshot)
            return data if isinstance(data, dict) else {"upstream": [], "downstream": []}
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
            "upvotes": self.upvotes,
            "downvotes": self.downvotes,
            "hit_count": self.hit_count,
            "recall_count": self.recall_count,
            "root_cause": self.root_cause[:2048],
            "solution": self.solution[:2048],
            "alert_template": self.alert_template[:1024],
            "topology_snapshot": self.topology_snapshot[:1024],
            "resolved_by": self.resolved_by,
            "kb_version": self.kb_version,
            "tenant_id": self.tenant_id,
            "environment": self.environment,
            "embedding": self.embedding,
            "created_at": self.created_at,
        }
