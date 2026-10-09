"""Read-only lookups for the web UI (#10): `GET /meta/labels` is the UI's only source of enum
labels (`LABELS_DE`). `GET /persons` moved to `app.api.household` (#13).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from fastapi import APIRouter

from app.domain.enums import (
    CATEGORY_GROUP,
    LABELS_DE,
    AllowanceShare,
    Anlage,
    AttentionReason,
    Bundesland,
    Category,
    CategoryGroup,
    DocType,
    FilingStatus,
    PaymentMethod,
    PersonKind,
    Religion,
    Steuerklasse,
)
from app.tax_params import supported_years

router = APIRouter()


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
        "bundeslaender": _codes(Bundesland),
        "filing_statuses": _codes(FilingStatus),
        "steuerklassen": _codes(Steuerklasse),
        "religions": _codes(Religion),
        "allowance_shares": _codes(AllowanceShare),
        "person_kinds": _codes(PersonKind),
        "supported_years": list(supported_years()),
    }
