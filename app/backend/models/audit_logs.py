from core.database import Base
from datetime import datetime as PyDateTime
from typing import Optional
from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column


class Audit_logs(Base):
    __tablename__ = "audit_logs"
    __table_args__ = {"extend_existing": True}

    action: Mapped[str] = mapped_column(String, nullable=False)
    actor: Mapped[str] = mapped_column(String, nullable=False)
    after_json: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    before_json: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True, autoincrement=True, nullable=False)
    ip: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    target_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    target_type: Mapped[str] = mapped_column(String, nullable=False)
    ua: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[Optional[PyDateTime]] = mapped_column(DateTime(timezone=True), default=PyDateTime.now)
    updated_at: Mapped[Optional[PyDateTime]] = mapped_column(DateTime(timezone=True), default=PyDateTime.now, onupdate=PyDateTime.now)