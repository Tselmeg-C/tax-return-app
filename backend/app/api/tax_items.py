"""Tax items api (#10): list, correct (PATCH) and add by hand (POST on a document).

Writes go through `app.tax_items` (version check, lock order, audit, counters). Error bodies
are codes plus the field name, never the sent value.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func

from app.api.deps import Scope, SignedIn
from app.api.documents import DocumentOut
from app.db.models import Document, TaxItem
from app.domain.enums import Anlage, Category, DocumentStatus, PaymentMethod
from app.tax_items import (
    Rejected,
    Result,
    TaxItemMetrics,
    VersionConflict,
    create_manual_item,
    update_item,
)
from app.tax_params import supported_years

router = APIRouter()

Filter = Literal["all", "relevant", "uncertain", "attention", "manual"]
UNCERTAIN = Decimal("0.5")


def _money(value: Decimal | None) -> str | None:
    return None if value is None else f"{value:.2f}"


class TaxItemOut(BaseModel):
    id: uuid.UUID
    version: int
    document_id: uuid.UUID | None
    year: int
    category: Category
    anlage: Anlage | None
    zeile: str | None
    gross_amount: str
    deductible_amount: str
    labour_share_35a: str | None
    vendor: str | None
    invoice_date: date | None
    payment_date: date | None
    payment_method: PaymentMethod
    is_relevant: bool
    reason: str | None
    confidence: str | None
    overridden_by_user: bool
    person_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    document: DocumentOut | None

    @classmethod
    def of(cls, item: TaxItem, doc: Document | None) -> TaxItemOut:
        return cls(
            id=item.id,
            version=item.version,
            document_id=item.document_id,
            year=item.year,
            category=item.category,
            anlage=item.anlage,
            zeile=item.zeile,
            gross_amount=f"{item.gross_amount:.2f}",
            deductible_amount=f"{item.deductible_amount:.2f}",
            labour_share_35a=_money(item.labour_share_35a),
            vendor=item.vendor,
            invoice_date=item.invoice_date,
            payment_date=item.payment_date,
            payment_method=item.payment_method,
            is_relevant=item.is_relevant,
            reason=item.reason,
            confidence=None if item.confidence is None else f"{item.confidence:.3f}",
            overridden_by_user=item.overridden_by_user,
            person_id=item.person_id,
            created_at=item.created_at,
            updated_at=item.updated_at,
            document=DocumentOut.of(doc) if doc is not None else None,
        )


class _Fields(BaseModel):
    # Money is `Any` here and checked by the service, so a bad amount answers
    # `invalid_amount` + field (a JSON number is refused too, never coerced).
    model_config = ConfigDict(extra="forbid")


class TaxItemPatch(_Fields):
    version: int
    category: Category | None = None
    is_relevant: bool | None = None
    gross_amount: Any = None
    deductible_amount: Any = None
    labour_share_35a: Any = None
    year: int | None = None
    person_id: uuid.UUID | None = None


class TaxItemCreate(_Fields):
    category: Category
    is_relevant: bool
    gross_amount: Any
    deductible_amount: Any
    year: int
    labour_share_35a: Any = None
    person_id: uuid.UUID | None = None


def _metrics(request: Request) -> TaxItemMetrics:
    found: TaxItemMetrics = request.app.state.tax_item_metrics
    return found


def _rejected(exc: Rejected) -> JSONResponse:
    body: dict[str, Any] = {"detail": exc.code}
    if exc.field is not None:
        body["field"] = exc.field
    return JSONResponse(body, status_code=exc.status)


def _out(result: Result) -> dict[str, Any]:
    return TaxItemOut.of(result.item, result.document).model_dump(mode="json")


@router.get("/tax-items")
async def list_tax_items(
    scope: Scope,
    year: Annotated[int | None, Query()] = None,
    filter: Annotated[Filter, Query()] = "all",  # noqa: A002 (the query name)
    document_id: Annotated[uuid.UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    stmt = scope.select(TaxItem).outerjoin(Document, Document.id == TaxItem.document_id)
    if document_id is not None:
        stmt = stmt.where(TaxItem.document_id == document_id)
    elif filter != "attention":
        if year is None:
            raise HTTPException(status_code=422, detail="invalid_request")
        if year not in supported_years():
            raise HTTPException(status_code=422, detail="unsupported_year")
        stmt = stmt.where(TaxItem.year == year)
    if filter == "relevant":
        stmt = stmt.where(TaxItem.is_relevant.is_(True))
    elif filter == "uncertain":
        stmt = stmt.where(TaxItem.confidence < UNCERTAIN, TaxItem.overridden_by_user.is_(False))
    elif filter == "attention":
        stmt = stmt.where(Document.status == DocumentStatus.NEEDS_ATTENTION)
    elif filter == "manual":
        stmt = stmt.where(TaxItem.overridden_by_user.is_(True))

    total = await scope.session.scalar(
        stmt.with_only_columns(func.count(TaxItem.id)).order_by(None)
    )
    rows = await scope.session.execute(
        stmt.add_columns(Document)
        .order_by(func.coalesce(Document.created_at, TaxItem.created_at).desc(), TaxItem.id.desc())
        .limit(limit)
        .offset(offset)
    )
    items = [TaxItemOut.of(item, doc) for item, doc in rows]
    return {"items": items, "total": int(total or 0)}


@router.patch("/tax-items/{item_id}")
async def patch_tax_item(
    request: Request, user: SignedIn, scope: Scope, item_id: uuid.UUID, body: TaxItemPatch
) -> JSONResponse:
    sent = {k: getattr(body, k) for k in body.model_fields_set if k != "version"}
    try:
        result = await update_item(
            scope, item_id, body.version, sent, user.user_id, _metrics(request)
        )
    except Rejected as exc:
        return _rejected(exc)
    except VersionConflict as exc:
        current = TaxItemOut.of(exc.item, exc.document).model_dump(mode="json")
        return JSONResponse({"detail": "version_conflict", "tax_item": current}, status_code=409)
    return JSONResponse(_out(result))


@router.post("/documents/{document_id}/tax-items", status_code=201)
async def create_tax_item(
    request: Request, user: SignedIn, scope: Scope, document_id: uuid.UUID, body: TaxItemCreate
) -> JSONResponse:
    sent = {k: getattr(body, k) for k in body.model_fields_set}
    try:
        result = await create_manual_item(scope, document_id, sent, user.user_id, _metrics(request))
    except Rejected as exc:
        return _rejected(exc)
    return JSONResponse(_out(result), status_code=201)
