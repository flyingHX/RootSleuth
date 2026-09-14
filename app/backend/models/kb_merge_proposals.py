from core.database import Base
from datetime import datetime as PyDateTime
from typing import Optional
from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column


class Kb_merge_proposals(Base):
    __tablename__ = "kb_merge_proposals"
    __table_args__ = {"extend_existing": True}

    approval_request_id: Mapped[Optional[int]] = mapped_column(Integer, index=True, nullable=True)
    created_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True, autoincrement=True, nullable=False)
    master_case_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    merged_case_ids: Mapped[str] = mapped_column(String, nullable=False)
    merge_strategy_json: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    status: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[Optional[PyDateTime]] = mapped_column(DateTime(timezone=True), default=PyDateTime.now)
    updated_at: Mapped[Optional[PyDateTime]] = mapped_column(DateTime(timezone=True), default=PyDateTime.now, onupdate=PyDateTime.now)