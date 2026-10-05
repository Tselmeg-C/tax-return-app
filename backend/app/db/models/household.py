from __future__ import annotations

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models._common import CreatedAt, UUIDPrimaryKey


class Household(UUIDPrimaryKey, CreatedAt, Base):
    """The tenant: one family. Every other table carries `household_id`."""

    __tablename__ = "household"

    name: Mapped[str] = mapped_column(String(100), nullable=False)
