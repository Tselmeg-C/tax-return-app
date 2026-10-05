"""`repr()` / `str()` of every model show only `<ClassName id=…>`, never column values."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

import pytest
import sqlalchemy as sa

from app.db.base import Base
from app.db.types import EncryptedJSON, EncryptedString
from tests.domain.conftest import steuer_id_sentinel, text_sentinel

MODELS = sorted((m.class_ for m in Base.registry.mappers), key=lambda c: c.__name__)
SENTINEL_DATE = date(1911, 11, 11)


def _fill(model: type[Any]) -> tuple[Any, list[str]]:
    sentinels: list[str] = []
    values: dict[str, Any] = {}
    for column in model.__table__.columns:
        kind = column.type
        if isinstance(kind, EncryptedString):
            value: Any = steuer_id_sentinel()
            sentinels.append(value)
        elif isinstance(kind, EncryptedJSON):
            inner = text_sentinel("json")
            value = {"nested": inner}
            sentinels.append(inner)
        elif isinstance(kind, sa.Enum):
            continue
        elif isinstance(kind, sa.String):
            value = text_sentinel("s")[: kind.length or 100]
            sentinels.append(value)
        elif isinstance(kind, sa.Date):
            value = SENTINEL_DATE
            sentinels.append("1911")
        else:
            continue
        values[column.key] = value
    return model(**values), sentinels


@pytest.mark.parametrize("model", MODELS, ids=lambda m: m.__name__)
def test_repr_shows_only_class_and_id(model: type[Any]) -> None:
    obj, sentinels = _fill(model)
    assert sentinels
    assert isinstance(obj.id, uuid.UUID)  # assigned at construction
    expected = f"<{model.__name__} id={obj.id}>"
    assert repr(obj) == expected
    assert str(obj) == expected
    assert f"{obj}" == expected
    for sentinel in sentinels:
        assert sentinel not in repr(obj)
        assert sentinel not in str(obj)


@pytest.mark.parametrize("model", MODELS, ids=lambda m: m.__name__)
def test_repr_not_overridden_per_model(model: type[Any]) -> None:
    assert "__repr__" not in vars(model)
    assert "__str__" not in vars(model)
