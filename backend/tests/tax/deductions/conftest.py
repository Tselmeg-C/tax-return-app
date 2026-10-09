from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.domain.enums import AttentionReason, Category, FilingStatus, PaymentMethod
from app.tax.deductions import ChildInput, EmploymentInput, ItemInput, ReturnContext
from app.tax_params import load_params


@pytest.fixture(params=[2025, 2026])
def year(request: pytest.FixtureRequest) -> int:
    return int(request.param)


@pytest.fixture
def wp(year: int):  # noqa: ANN201
    return load_params(year).werbungskosten


@pytest.fixture
def sp(year: int):  # noqa: ANN201
    return load_params(year).sonderausgaben


def ctx(year: int = 2025, joint: bool = False) -> ReturnContext:
    return ReturnContext(
        year, FilingStatus.JOINT if joint else FilingStatus.SINGLE, "A", "B" if joint else None
    )


_n = 0


def item(
    category: Category,
    amount: str | Decimal,
    person: str | None = "A",
    *,
    year: int = 2025,
    pay: PaymentMethod = PaymentMethod.BANK_TRANSFER,
    relevant: bool = True,
    override: bool = False,
    reason: AttentionReason | None = None,
    id: str | None = None,
) -> ItemInput:
    global _n
    _n += 1
    return ItemInput(
        id=id or f"i{_n}",
        person_id=person,
        category=category,
        year=year,
        deductible_amount=Decimal(amount),
        labour_share_35a=None,
        payment_method=pay,
        is_relevant=relevant,
        overridden_by_user=override,
        attention_reason=reason,
    )


def emp(person: str = "A", km: int | None = None, office: int = 0, ho: int = 0) -> EmploymentInput:
    return EmploymentInput(person, km, office, ho)


def child(
    id: str = "K1",
    dob: date = date(2020, 5, 5),
    *,
    grade: int | None = None,
    months: int = 12,
    house: bool = True,
) -> ChildInput:
    return ChildInput(id, dob, grade, months, False, house)
