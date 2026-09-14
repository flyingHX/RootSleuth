from core.database import Base
from datetime import datetime as PyDateTime
from typing import Optional
from sqlalchemy import DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column


class Kb_cases(Base):
    __tablename__ = "kb_cases"
    __table_args__ = {"extend_existing": True}

    alert_template: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    case_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    cluster: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    error_type: Mapped[str] = mapped_column(String, nullable=False)
    feedback_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    fingerprint: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True, autoincrement=True, nullable=False)
    root_cause: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    service_name: Mapped[str] = mapped_column(String, nullable=False)
    solution: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    status: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    topology_snapshot: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    version: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[Optional[PyDateTime]] = mapped_column(DateTime(timezone=True), default=PyDateTime.now)
    updated_at: Mapped[Optional[PyDateTime]] = mapped_column(DateTime(timezone=True), default=PyDateTime.now, onupdate=PyDateTime.now)