"""Household scoping: query household-owned tables only through `HouseholdScope`.

Every table except `household` carries `household_id`. A scope only ever sees rows of its own
household: `get` of another household's id returns `None` (it does not reveal that the row
exists). #5's `current_user` dependency builds the scope for a request.
"""

from __future__ import annotations

import uuid
from typing import Any, TypeVar

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import Base

M = TypeVar("M", bound=Base)


def is_household_owned(model: type[Base]) -> bool:
    table = getattr(model, "__table__", None)
    return table is not None and "household_id" in table.c


def _require_owned(model: type[Base]) -> None:
    if not is_household_owned(model):
        raise TypeError(f"{model.__name__} is not household-owned (no household_id column)")


class HouseholdScope:
    """Household-filtered access to one `AsyncSession`."""

    def __init__(self, session: AsyncSession, household_id: uuid.UUID) -> None:
        self.session = session
        self.household_id = household_id

    def select(self, model: type[M]) -> Select[M]:
        """`SELECT model ... WHERE household_id = <scope>`; add further filters as usual."""
        _require_owned(model)
        column: Any = model.__table__.c.household_id
        return select(model).where(column == self.household_id)

    async def get(self, model: type[M], ident: uuid.UUID) -> M | None:
        """The row with this id, or `None` if it does not exist or belongs to another household."""
        id_column: Any = model.__table__.c.id
        stmt = self.select(model).where(id_column == ident)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    def add(self, obj: Base) -> None:
        """Add `obj` to the session, setting `household_id` if unset.

        Raises `ValueError` if `obj` already belongs to another household.
        """
        _require_owned(type(obj))
        current = getattr(obj, "household_id", None)
        if current is None:
            setattr(obj, "household_id", self.household_id)  # noqa: B010 (Base has no attr)
        elif current != self.household_id:
            raise ValueError("object belongs to another household")
        self.session.add(obj)
