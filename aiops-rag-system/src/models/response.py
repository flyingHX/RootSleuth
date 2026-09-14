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
    alert_template: str = ""
    root_cause: str = ""
    solution: str = ""
    start_time: int = 0


class DiagnosticResult(BaseModel):
    event_id: str
    root_cause: str
    solution: str
    confidence: float
    suggest_actions: List[str] = Field(default_factory=list)
    similar_cases: List[DiagnosticCaseRef] = Field(default_factory=list)
    latency_ms: int = 0
    is_fallback: bool = False
    reason: Optional[str] = None


class FeedbackResponse(BaseModel):
    status: str = "success"
    case_id: str = ""
    feedback_score: int = 0
