"""User writes to `tax_item` (#10): PATCH (correct / "Passt so") and the manual item.

Every user write goes through `update_item` / `create_manual_item`: they set
`overridden_by_user` (which #9's pipeline never overwrites), check `version`, lock the
document before the item, write exactly one `tax_item` audit row (plus one `document` row
when a needs-attention document is resolved) and emit the override counters after the
commit. Logs and metrics carry ids, field names and codes only, never values.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import structlog
from opentelemetry import metrics
from opentelemetry.metrics import MeterProvider

from app.db.audit import Actor, record, snapshot
from app.db.models import Document, Person, TaxItem
from app.db.scope import HouseholdScope
from app.domain.enums import (
    CATEGORY_GROUP,
    AttentionReason,
    AuditAction,
    Category,
    CategoryGroup,
    DocumentStatus,
)
from app.tax.mapping import map_category
from app.tax_params import mapping_table, supported_years

log = structlog.stdlib.get_logger("app.tax_items")

MONEY_RE = re.compile(r"^-?\d{1,9}\.\d{2}$")
ZERO = Decimal("0.00")
MONEY_FIELDS = ("gross_amount", "deductible_amount", "labour_share_35a")
EDITABLE = ("category", "is_relevant", *MONEY_FIELDS, "year", "person_id")
NOT_NULL = {"category", "is_relevant", "gross_amount", "deductible_amount", "year"}
METRIC_FIELD = {"person_id": "person"}  # metric attribute value per column
BUSY = (DocumentStatus.QUEUED, DocumentStatus.PROCESSING)
MANUAL_REASONS = (
    AttentionReason.CLASSIFICATION_FAILED,
    AttentionReason.EXTRACTION_FAILED,
    AttentionReason.UNREADABLE,
    AttentionReason.MULTIPLE_DOCUMENTS,
)


class Rejected(Exception):
    """A refused write: `status` + `code` (+ the offending `field`). Never carries a value."""

    def __init__(self, status: int, code: str, field: str | None = None) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.field = field


class VersionConflict(Exception):
    def __init__(self, item: TaxItem, document: Document | None) -> None:
        super().__init__("version_conflict")
        self.item = item
        self.document = document


class TaxItemMetrics:
    """`belegbot.tax_item.{overrides, reviews, manual_items}` (codes and field names only)."""

    def __init__(self, provider: MeterProvider | None = None) -> None:
        meter = (provider or metrics.get_meter_provider()).get_meter("belegbot")
        self.overrides = meter.create_counter(
            "belegbot.tax_item.overrides", unit="{field}", description="Fields changed by users"
        )
        self.reviews = meter.create_counter(
            "belegbot.tax_item.reviews", unit="{review}", description="Successful item PATCHes"
        )
        self.manual_items = meter.create_counter(
            "belegbot.tax_item.manual_items", unit="{item}", description="Items added by hand"
        )


@dataclass
class Result:
    item: TaxItem
    document: Document | None


def _money(field: str, value: Any) -> Decimal:
    if not isinstance(value, str) or not MONEY_RE.fullmatch(value):
        raise Rejected(422, "invalid_amount", field)
    return Decimal(value)


def _merge(
    current: dict[str, Any], sent: dict[str, Any], *, known_person: Callable[[uuid.UUID], bool]
) -> dict[str, Any]:
    """The editable values after applying `sent` to `current` (Scope's validation table)."""
    for key, value in sent.items():
        if value is None and key in NOT_NULL:
            raise Rejected(422, "invalid_request", key)
    new = dict(current)
    for key, value in sent.items():
        new[key] = _money(key, value) if key in MONEY_FIELDS and value is not None else value
    if (
        "year" in sent
        and new["year"] != current.get("year")
        and new["year"] not in (supported_years())
    ):
        raise Rejected(422, "unsupported_year", "year")

    if new["category"] is Category.IRRELEVANT:
        if sent.get("is_relevant") is True:
            raise Rejected(422, "category_irrelevant", "is_relevant")
        new["is_relevant"] = False
    if not new["is_relevant"]:
        if "deductible_amount" in sent and new["deductible_amount"] != ZERO:
            raise Rejected(422, "irrelevant_not_deductible", "deductible_amount")
        new["deductible_amount"] = ZERO

    household_level = CATEGORY_GROUP[new["category"]] is CategoryGroup.HAUSHALTSNAHE
    if not household_level:
        if sent.get("labour_share_35a") is not None:
            raise Rejected(422, "labour_share_not_allowed", "labour_share_35a")
        new["labour_share_35a"] = None
    else:
        if sent.get("person_id") is not None:
            raise Rejected(422, "person_not_allowed", "person_id")
        new["person_id"] = None

    gross, deductible = new["gross_amount"], new["deductible_amount"]
    if deductible != ZERO and (abs(deductible) > abs(gross) or (deductible < 0) != (gross < 0)):
        raise Rejected(422, "deductible_exceeds_gross", "deductible_amount")
    share = new["labour_share_35a"]
    if share is not None and not ZERO <= share <= abs(gross):
        raise Rejected(422, "labour_share_invalid", "labour_share_35a")
    person = sent.get("person_id")
    if person is not None and not known_person(person):
        raise Rejected(422, "unknown_person", "person_id")

    if new["category"] != current.get("category") or new["year"] != current.get("year"):
        line = map_category(new["category"], new["year"], mapping_table())
        new["anlage"], new["zeile"] = line.anlage, line.zeile
    return new


async def _persons(scope: HouseholdScope, sent: dict[str, Any]) -> set[uuid.UUID]:
    person = sent.get("person_id")
    if person is None:
        return set()
    found = await scope.get(Person, person)
    return {found.id} if found is not None else set()


async def _lock_document(scope: HouseholdScope, document_id: uuid.UUID) -> Document | None:
    stmt = scope.select(Document).where(Document.id == document_id).with_for_update()
    result = await scope.session.execute(stmt.execution_options(populate_existing=True))
    return result.scalar_one_or_none()


async def _resolve_attention(scope: HouseholdScope, doc: Document | None, actor: Actor) -> None:
    """Saving an item of a needs-attention document marks it done (Decision 5)."""
    if doc is None or doc.status is not DocumentStatus.NEEDS_ATTENTION:
        return
    before = snapshot(doc)
    doc.status = DocumentStatus.DONE
    doc.attention_reason = None
    await scope.session.flush()
    await record(
        scope.session,
        household_id=scope.household_id,
        entity="document",
        entity_id=doc.id,
        action=AuditAction.UPDATE,
        before=before,
        after=snapshot(doc),
        actor=actor,
    )


async def update_item(
    scope: HouseholdScope,
    item_id: uuid.UUID,
    version: int,
    sent: dict[str, Any],
    user_id: uuid.UUID,
    tax_metrics: TaxItemMetrics,
) -> Result:
    """PATCH: merge `sent` (editable fields only), commit, then count. Raises `Rejected` /
    `VersionConflict`; the session is rolled back on every refusal."""
    session = scope.session
    try:
        found = await scope.get(TaxItem, item_id)
        if found is None:
            raise Rejected(404, "not_found")
        doc = None
        if found.document_id is not None:
            doc = await _lock_document(scope, found.document_id)  # document before item
            if doc is None:
                raise Rejected(404, "not_found")
            if doc.status in BUSY:
                raise Rejected(409, "document_busy")
        locked = scope.select(TaxItem).where(TaxItem.id == item_id).with_for_update()
        item = (
            await session.execute(locked.execution_options(populate_existing=True))
        ).scalar_one_or_none()
        if item is None:  # replaced by a pipeline run that committed first
            raise Rejected(404, "not_found")
        if item.version != version:
            raise VersionConflict(item, doc)

        current = {key: getattr(item, key) for key in (*EDITABLE, "anlage", "zeile")}
        persons = await _persons(scope, sent)
        new = _merge(current, sent, known_person=persons.__contains__)
        changed = [k for k in sent if new[k] != current[k]]
        origin = "user" if item.overridden_by_user else "pipeline"
        category_before = item.category

        before = snapshot(item)
        for key, value in new.items():
            if value != current[key]:
                setattr(item, key, value)
        if not item.overridden_by_user:
            item.overridden_by_user = True
        await session.flush()
        actor = Actor.user(user_id)
        await record(
            session,
            household_id=scope.household_id,
            entity="tax_item",
            entity_id=item.id,
            action=AuditAction.UPDATE,
            before=before,
            after=snapshot(item),
            actor=actor,
        )
        await _resolve_attention(scope, doc, actor)
        await session.commit()
    except BaseException as exc:
        if isinstance(exc, VersionConflict):  # keep the loaded values for the 409 body
            session.expunge(exc.item)
            if exc.document is not None:
                session.expunge(exc.document)
            log.info("tax_item.version_conflict", tax_item_id=str(item_id))
        elif isinstance(exc, Rejected):
            log.info("tax_item.rejected", code=exc.code)
        await session.rollback()
        raise

    for key in changed:
        tax_metrics.overrides.add(
            1,
            {
                "field": METRIC_FIELD.get(key, key),
                "category": category_before.value,
                "origin": origin,
            },
        )
    tax_metrics.reviews.add(1, {"outcome": "changed" if changed else "confirmed", "origin": origin})
    log.info(
        "tax_item.updated",
        tax_item_id=str(item.id),
        document_id=str(item.document_id) if item.document_id else None,
        fields=[METRIC_FIELD.get(k, k) for k in changed],
        origin=origin,
        version=item.version,
        actor_user_id=str(user_id),
    )
    return Result(item, doc)


async def create_manual_item(
    scope: HouseholdScope,
    document_id: uuid.UUID,
    sent: dict[str, Any],
    user_id: uuid.UUID,
    tax_metrics: TaxItemMetrics,
) -> Result:
    """POST /documents/{id}/tax-items (Decision 6): an item for a document the pipeline
    could not read. Raises `Rejected`; the session is rolled back on every refusal."""
    session = scope.session
    try:
        doc = await _lock_document(scope, document_id)
        if doc is None:
            raise Rejected(404, "not_found")
        if doc.status in BUSY:
            raise Rejected(409, "document_busy")
        existing = await session.execute(
            scope.select(TaxItem).with_only_columns(TaxItem.id).where(TaxItem.document_id == doc.id)
        )
        if existing.first() is not None:
            raise Rejected(409, "tax_item_exists")
        reason = doc.attention_reason
        if doc.status is not DocumentStatus.NEEDS_ATTENTION or reason not in MANUAL_REASONS:
            raise Rejected(409, "manual_item_not_allowed")
        assert reason is not None
        persons = await _persons(scope, sent)
        empty: dict[str, Any] = {
            "labour_share_35a": None,
            "person_id": None,
            "anlage": None,
            "zeile": None,
        }
        new = _merge(empty, sent, known_person=persons.__contains__)
        item = TaxItem(document_id=doc.id, overridden_by_user=True, **new)
        scope.add(item)
        await session.flush()
        actor = Actor.user(user_id)
        await record(
            session,
            household_id=scope.household_id,
            entity="tax_item",
            entity_id=item.id,
            action=AuditAction.CREATE,
            before=None,
            after=snapshot(item),
            actor=actor,
        )
        await _resolve_attention(scope, doc, actor)
        await session.commit()
    except BaseException as exc:
        await session.rollback()
        if isinstance(exc, Rejected):
            log.info("tax_item.rejected", code=exc.code)
        raise

    tax_metrics.manual_items.add(1, {"attention_reason": reason.value})
    log.info(
        "tax_item.created_manual",
        tax_item_id=str(item.id),
        document_id=str(doc.id),
        attention_reason=reason.value,
    )
    return Result(item, doc)


__all__ = [
    "MANUAL_REASONS",
    "Rejected",
    "Result",
    "TaxItemMetrics",
    "VersionConflict",
    "create_manual_item",
    "update_item",
]
