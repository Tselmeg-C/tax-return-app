"""Read-only lookups for the web UI (#10): the household's persons and the German labels.

`GET /persons` returns names only (never the Steuer-ID or other fields); #13 adds create /
edit. `GET /meta/labels` is the UI's only source of enum labels (`LABELS_DE`).
"""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import case

from app.api.deps import Scope
from app.db.models import Person
from app.domain.enums import (
    CATEGORY_GROUP,
    LABELS_DE,
    Anlage,
    AttentionReason,
    Category,
    CategoryGroup,
    DocType,
    PaymentMethod,
    PersonKind,
)
from app.tax_params import supported_years

router = APIRouter()


class PersonOut(BaseModel):
    id: uuid.UUID
    kind: PersonKind
    first_name: str
    last_name: str | None


@router.get("/persons")
async def list_persons(scope: Scope) -> list[PersonOut]:
    stmt = scope.select(Person).order_by(
        case((Person.kind == PersonKind.ADULT, 0), else_=1), Person.first_name, Person.id
    )
    persons = (await scope.session.execute(stmt)).scalars().all()
    return [
        PersonOut(id=p.id, kind=p.kind, first_name=p.first_name, last_name=p.last_name)
        for p in persons
    ]


def _codes(enum: type[StrEnum]) -> list[dict[str, str]]:
    return [{"code": member.value, "label": LABELS_DE[enum][member]} for member in enum]


@router.get("/meta/labels")
async def labels() -> dict[str, Any]:
    groups = LABELS_DE[CategoryGroup]
    return {
        "categories": [
            {
                "code": c.value,
                "label": LABELS_DE[Category][c],
                "group": CATEGORY_GROUP[c].value,
                "group_label": groups[CATEGORY_GROUP[c]],
            }
            for c in Category
        ],
        "anlagen": _codes(Anlage),
        "attention_reasons": _codes(AttentionReason),
        "payment_methods": _codes(PaymentMethod),
        "doc_types": _codes(DocType),
        "supported_years": list(supported_years()),
    }
