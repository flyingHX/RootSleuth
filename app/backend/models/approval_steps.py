from core.database import Base
from datetime import datetime as PyDateTime
from typing import Optional
from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column


class Approval_steps(Base):
    __tablename__ = "approval_steps"
    __table_args__ = {"extend_existing": True}

    action: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    acted_at: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    approver: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    approver_role: Mapped[str] = mapped_column(String, nullable=False)
    comment: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True, autoincrement=True, nullable=False)
    request_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    step_no: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[Optional[PyDateTime]] = mapped_column(DateTime(timezone=True), default=PyDateTime.now)
    updated_at: Mapped[Optional[PyDateTime]] = mapped_column(DateTime(timezone=True), default=PyDateTime.now, onupdate=PyDateTime.now)