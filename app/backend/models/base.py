from datetime import datetime
from typing import Optional

from core.database import Base
from sqlalchemy import DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

__all__ = ["Base", "BaseModel"]


class BaseModel(Base):
    __abstract__ = True

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # Optional keeps these columns nullable, matching the schema of databases
    # created by earlier template versions (values still come from the server
    # defaults, so they are populated in practice).
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
