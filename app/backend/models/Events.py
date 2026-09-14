from core.database import Base
from datetime import datetime as PyDateTime
from typing import Optional
from sqlalchemy import DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column


class Events(Base):
    __tablename__ = "events"
    __table_args__ = {"extend_existing": True}

    ai_command: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    ai_output_json: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    ai_root_cause: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    ai_solution: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    candidates_json: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    cluster: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    degraded_reason: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    error_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    event_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    fingerprint: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True, autoincrement=True, nullable=False)
    rag_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    rag_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    rag_status: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    raw_log: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    service_name: Mapped[str] = mapped_column(String, nullable=False)
    severity: Mapped[str] = mapped_column(String, nullable=False)
    std_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    status: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    template: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    topology: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[Optional[PyDateTime]] = mapped_column(DateTime(timezone=True), default=PyDateTime.now)
    updated_at: Mapped[Optional[PyDateTime]] = mapped_column(DateTime(timezone=True), default=PyDateTime.now, onupdate=PyDateTime.now)