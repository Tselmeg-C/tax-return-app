"""Declarative base. `Base.metadata` is Alembic's `target_metadata`; models subclass `Base`."""

from typing import Any

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class SafeReprMixin:
    """`repr()` / `str()` show only the class name and id, never column values (PII).

    Do not override `__repr__` / `__str__` in models.
    """

    def __repr__(self) -> str:
        ident: Any = self.__dict__.get("id")  # no attribute load (no lazy SQL, no expiry)
        return f"<{type(self).__name__} id={ident}>"

    __str__ = __repr__


class Base(SafeReprMixin, DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


metadata = Base.metadata
