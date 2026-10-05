"""Audit helper: append a change to `audit_log` inside the caller's transaction.

Usage (update)::

    before = snapshot(item)
    item.deductible_amount = Decimal("80.00")
    await record(session, household_id=hh, entity="tax_item", entity_id=item.id,
                 action=AuditAction.UPDATE, before=before, after=snapshot(item),
                 actor=Actor.user(user_id))

- `create` stores only `after`, `delete` only `before`; `update` stores only changed keys and
  writes nothing (returns `None`) when nothing changed.
- Encrypted columns are compared on the plaintext but always stored as `"[redacted]"`.
- `record` never commits: a rolled-back transaction leaves no audit row.
- Audit rows are append-only by convention; there is no update or delete helper.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from sqlalchemy import inspect as sa_inspect
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import Base
from app.db.models import AuditLog
from app.db.types import EncryptedType
from app.domain.enums import ActorType, AuditAction

REDACTED = "[redacted]"


@dataclass(frozen=True, slots=True)
class Actor:
    type: ActorType
    user_id: uuid.UUID | None = None

    @classmethod
    def user(cls, user_id: uuid.UUID) -> Actor:
        return cls(ActorType.USER, user_id)

    @classmethod
    def system(cls) -> Actor:
        return cls(ActorType.SYSTEM, None)


def _json_ready(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, str):
        return str(value)
    if isinstance(value, Decimal | uuid.UUID):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _json_ready(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_json_ready(v) for v in value]
    raise TypeError(f"cannot snapshot value of type {type(value).__name__}")


def snapshot(obj: Base) -> dict[str, Any]:
    """Column values of a loaded instance as JSON-ready values (keys are column names).

    Encrypted columns appear as plaintext here; `record` redacts them before storing.
    """
    mapper = sa_inspect(type(obj))
    return {
        attr.columns[0].name: _json_ready(getattr(obj, attr.key)) for attr in mapper.column_attrs
    }


def _encrypted_columns(entity: str) -> set[str]:
    table = Base.metadata.tables[entity]
    return {c.name for c in table.columns if isinstance(c.type, EncryptedType)}


def _redact(values: dict[str, Any] | None, encrypted: set[str]) -> dict[str, Any] | None:
    if values is None:
        return None
    return {k: (REDACTED if k in encrypted else v) for k, v in values.items()}


async def record(
    session: AsyncSession,
    *,
    household_id: uuid.UUID,
    entity: str,
    entity_id: uuid.UUID,
    action: AuditAction,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    actor: Actor,
) -> AuditLog | None:
    """Add one audit row to the session (and flush). Returns `None` for a no-op update."""
    if entity not in Base.metadata.tables:
        raise ValueError("unknown audit entity (must be a table name)")
    encrypted = _encrypted_columns(entity)

    if action is AuditAction.CREATE:
        if after is None:
            raise ValueError("create needs `after`")
        stored_before, stored_after = None, dict(after)
    elif action is AuditAction.DELETE:
        if before is None:
            raise ValueError("delete needs `before`")
        stored_before, stored_after = dict(before), None
    else:
        if before is None or after is None:
            raise ValueError("update needs `before` and `after`")
        changed = [k for k in before.keys() | after.keys() if before.get(k) != after.get(k)]
        if not changed:
            return None
        stored_before = {k: before.get(k) for k in sorted(changed)}
        stored_after = {k: after.get(k) for k in sorted(changed)}

    row = AuditLog(
        household_id=household_id,
        entity=entity,
        entity_id=entity_id,
        action=action,
        before=_redact(stored_before, encrypted),
        after=_redact(stored_after, encrypted),
        actor_type=actor.type,
        actor_user_id=actor.user_id,
    )
    session.add(row)
    await session.flush()
    return row
