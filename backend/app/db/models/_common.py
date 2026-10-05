"""Column mixins shared by the domain models (see `CLAUDE.md`, "New tables")."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, event, func
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from app.db.base import Base


class UUIDPrimaryKey:
    """`id uuid PRIMARY KEY`, generated in Python (no server default).

    The id is assigned when the object is constructed (see `_assign_id`), so related rows and
    audit entries can reference it before the first flush.
    """

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)


class HouseholdOwned:
    """`household_id uuid NOT NULL REFERENCES household(id) ON DELETE RESTRICT`.

    Every table except `household` uses this. The column is not indexed here: each table
    declares an index (or composite index) starting with `household_id` in `__table_args__`.
    """

    @declared_attr
    def household_id(cls) -> Mapped[uuid.UUID]:
        return mapped_column(ForeignKey("household.id", ondelete="RESTRICT"), nullable=False)


class CreatedAt:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Timestamps(CreatedAt):
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


@event.listens_for(Base, "init", propagate=True)
def _assign_id(target: Any, args: Any, kwargs: dict[str, Any]) -> None:
    if isinstance(target, UUIDPrimaryKey) and kwargs.get("id") is None:
        kwargs["id"] = uuid.uuid4()
