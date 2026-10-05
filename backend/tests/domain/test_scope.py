"""`HouseholdScope` only ever sees rows of its own household."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import Base
from app.db.models import Household, Person
from app.db.scope import HouseholdScope, is_household_owned
from app.domain.enums import PersonKind
from tests.domain.factories import World, make_household, make_world

OWNED_MODELS = sorted(
    (m.class_ for m in Base.registry.mappers if is_household_owned(m.class_)),
    key=lambda c: c.__name__,
)


def test_every_non_household_model_is_owned() -> None:
    all_models = {m.class_ for m in Base.registry.mappers}
    assert all_models - set(OWNED_MODELS) == {Household}


@pytest.fixture
async def worlds(db_session: AsyncSession) -> tuple[World, World]:
    return await make_world(db_session, "A"), await make_world(db_session, "B")


@pytest.mark.parametrize("model", OWNED_MODELS, ids=lambda m: m.__name__)
async def test_select_and_get_are_scoped(
    db_session: AsyncSession, worlds: tuple[World, World], model: type[Any]
) -> None:
    a, b = worlds
    assert model in a.by_model(), f"add {model.__name__} to tests.domain.factories.World"
    row_a, row_b = a.by_model()[model], b.by_model()[model]
    scope_a = HouseholdScope(db_session, a.household.id)

    rows = (await db_session.execute(scope_a.select(model))).scalars().all()
    assert {row.id for row in rows} == {row_a.id}

    assert await scope_a.get(model, row_b.id) is None
    found = await scope_a.get(model, row_a.id)
    assert found is not None and found.id == row_a.id


async def test_add_sets_or_checks_household(db_session: AsyncSession) -> None:
    a = await make_household(db_session, "A")
    b = await make_household(db_session, "B")
    scope_a = HouseholdScope(db_session, a.id)

    foreign = Person(household_id=b.id, kind=PersonKind.ADULT, first_name="Testperson")
    with pytest.raises(ValueError):
        scope_a.add(foreign)

    own = Person(kind=PersonKind.ADULT, first_name="Testperson")
    scope_a.add(own)
    assert own.household_id == a.id
    await db_session.flush()

    same = Person(household_id=a.id, kind=PersonKind.ADULT, first_name="Testperson")
    scope_a.add(same)
    await db_session.flush()


async def test_unowned_model_raises_type_error(db_session: AsyncSession) -> None:
    hh = await make_household(db_session)
    scope = HouseholdScope(db_session, hh.id)
    with pytest.raises(TypeError):
        scope.select(Household)
    with pytest.raises(TypeError):
        await scope.get(Household, hh.id)
    with pytest.raises(TypeError):
        scope.add(Household(name="X"))
