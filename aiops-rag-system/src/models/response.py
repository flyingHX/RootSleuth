"""API 响应模型。"""
from pydantic import BaseModel, Field
from typing import List, Optional


class WebhookResponse(BaseModel):
    status: str = "success"
    event_id: str = ""
    error_type: str = ""
    confidence: float = 0.0


class DiagnosticCaseRef(BaseModel):
    case_id: str
    similarity: float = 0.0
    final_score: float = 0.0
    # 四层重排分层数据（供前端漏斗展示与归因）
    l2_score: Optional[float] = None
    l3_score: Optional[float] = None
    l4_score: Optional[float] = None
    rerank_reason: Optional[str] = None
    alert_template: str = ""
    root_cause: str = ""
    solution: str = ""
    start_time: int = 0


class RerankLayerStats(BaseModel):
    """四层重排漏斗单次执行统计（L1 输入 -> L1 输出 -> L2 输出 -> L3 输出 -> 最终 Top-K）。"""

    input_count: int = 0
    l1_out: int = 0
    l2_out: int = 0
    l3_out: int = 0
    final_out: int = 0
    l3_available: bool = False
    l4_status: str = "skipped"  # ok / degraded / skipped


class DiagnosticResult(BaseModel):
    event_id: str
    root_cause: str
    solution: str
    confidence: float
    suggest_actions: List[str] = Field(default_factory=list)
    similar_cases: List[DiagnosticCaseRef] = Field(default_factory=list)
    rerank_stats: Optional[RerankLayerStats] = None
    latency_ms: int = 0
    is_fallback: bool = False
    reason: Optional[str] = None


class FeedbackResponse(BaseModel):
    status: str = "success"
    case_id: str = ""
    feedback_score: int = 0
