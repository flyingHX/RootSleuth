"""全局数据模型：标准化事件与原始告警。"""
from pydantic import BaseModel, Field
from typing import Optional, Dict, List


class RawAlert(BaseModel):
    """原始告警/日志"""
    source: str  # zabbix, apm, log
    raw_message: str
    labels: Dict[str, str] = Field(default_factory=dict)
    timestamp: int


class StandardizedEvent(BaseModel):
    """标准化后的事件（输出到 Kafka）"""
    event_id: str
    fingerprint: str
    service_name: str
    cluster: str
    namespace: Optional[str] = None
    error_type: str  # 如 redis_timeout
    severity: int = 2  # 1=info, 2=warning, 3=critical
    confidence: float = 0.0
    template: str = ""
    raw_log: str = ""
    topology: Dict[str, List[str]] = Field(default_factory=dict)
    timestamp: int = 0
