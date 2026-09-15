"""RAG 同步补偿任务模型：同步失败入队、指数退避重试、死信与人工重放。

闭环语义（评审 P0-1 增强）：
- 控制台 → RAG 的 upsert/delete/feedback/cache_invalidate 同步失败时，
  不再只留审计记录，而是固化为可执行补偿任务（幂等键去重）；
- 任务由 `services/rag_sync.retry_due_tasks` 按指数退避自动重试，
  超过 max_attempts 进入死信（dead），由运维通过 API 人工重放。
"""
from core.database import Base
from datetime import datetime as PyDateTime
from typing import Optional
from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column


class Rag_sync_tasks(Base):
    __tablename__ = "rag_sync_tasks"
    __table_args__ = {"extend_existing": True}

    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    case_id: Mapped[Optional[str]] = mapped_column(String(128), index=True, nullable=True)
    created_by: Mapped[Optional[str]] = mapped_column(String(191), nullable=True)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True, autoincrement=True, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(191), unique=True, index=True, nullable=False)
    last_attempt_at: Mapped[Optional[PyDateTime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_http_status: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5, server_default="5")
    next_retry_at: Mapped[Optional[PyDateTime]] = mapped_column(DateTime(timezone=True), index=True, nullable=True)
    payload_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), index=True, nullable=False, default="pending", server_default="pending"
    )
    task_type: Mapped[str] = mapped_column(String(32), nullable=False)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    verify_detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[Optional[PyDateTime]] = mapped_column(DateTime(timezone=True), default=PyDateTime.now)
    updated_at: Mapped[Optional[PyDateTime]] = mapped_column(
        DateTime(timezone=True), default=PyDateTime.now, onupdate=PyDateTime.now
    )
