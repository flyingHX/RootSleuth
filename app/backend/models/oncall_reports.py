from core.database import Base
from datetime import datetime as PyDateTime
from typing import Optional
from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column


class Oncall_reports(Base):
    __tablename__ = "oncall_reports"
    __table_args__ = {"extend_existing": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True, autoincrement=True, nullable=False)
    time_window: Mapped[str] = mapped_column(String, nullable=False)
    event_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    critical_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    warning_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    info_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    affected_systems: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    report_json: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    chatops_text: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    session_id: Mapped[Optional[int]] = mapped_column(Integer, index=True, nullable=True)
    actor: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[Optional[PyDateTime]] = mapped_column(DateTime(timezone=True), default=PyDateTime.now)
    updated_at: Mapped[Optional[PyDateTime]] = mapped_column(DateTime(timezone=True), default=PyDateTime.now, onupdate=PyDateTime.now)