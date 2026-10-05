from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import CheckConstraint, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models._common import CreatedAt, HouseholdOwned, UUIDPrimaryKey
from app.db.types import enum_type
from app.domain.enums import ActorType, AuditAction


class AuditLog(UUIDPrimaryKey, HouseholdOwned, CreatedAt, Base):
    """Append-only change log (by convention; DB guard in #24). Write via `app.db.audit.record`.

    `entity_id` and `actor_user_id` have no FK on purpose: audit rows outlive the entity.
    """

    __tablename__ = "audit_log"
    __table_args__ = (
        CheckConstraint(
            "(actor_type = 'user') = (actor_user_id IS NOT NULL)",
            name="actor_user_id_matches_type",
        ),
        Index("ix_audit_log_household_id_entity_entity_id", "household_id", "entity", "entity_id"),
        Index("ix_audit_log_household_id_created_at", "household_id", "created_at"),
    )

    entity: Mapped[str] = mapped_column(String(63), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    action: Mapped[AuditAction] = mapped_column(enum_type(AuditAction, "action"), nullable=False)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    actor_type: Mapped[ActorType] = mapped_column(
        enum_type(ActorType, "actor_type"), nullable=False
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
