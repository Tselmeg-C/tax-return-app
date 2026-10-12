"""§ 35a EStG (#77): haushaltsnahe Dienstleistungen (Abs. 2) and Handwerkerleistungen (Abs. 3).

A Steuerermäßigung, not a deduction from the income: #17 subtracts `total` from the tarifliche
Einkommensteuer (never below 0, no carry-over). Per category `min(cap, 20 % x labour sum)`,
rounded down to cents; one cap per return (§ 35a Abs. 5 Satz 4 for two single people in one
household, a joint return has one household). The amount of an item is `min(deductible_amount,
labour_share_35a)`; cash is excluded (Abs. 5 Satz 3: invoice and payment to the provider's account).
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import ROUND_DOWN, Decimal

from app.domain.enums import Category, DeductionNote, PaymentMethod
from app.tax.deductions.models import (
    HaushaltsnaheLine,
    HaushaltsnaheResult,
    ItemInput,
    Note,
    ReturnContext,
)
from app.tax.deductions.select import finish_notes, net_by_key, select_items
from app.tax.models import HaushaltsnaheParams

_CENT = Decimal("0.01")
_CATEGORIES = frozenset({Category.HAUSHALTSNAHE_DIENSTLEISTUNG, Category.HANDWERKERLEISTUNG})


def _line(labour: Decimal, rate: Decimal, cap: Decimal) -> HaushaltsnaheLine:
    raw = (rate * labour).quantize(_CENT, rounding=ROUND_DOWN)  # labour >= 0 here
    return HaushaltsnaheLine(labour, min(cap, raw).quantize(_CENT), cap, raw >= cap)


def haushaltsnahe(
    ctx: ReturnContext, items: Iterable[ItemInput], p: HaushaltsnaheParams
) -> HaushaltsnaheResult:
    notes: list[Note] = []
    rows: list[tuple[str | None, Category, Decimal]] = []
    for s in select_items(ctx, items, _CATEGORIES, notes):
        item = s.item
        if item.payment_method is PaymentMethod.CASH:  # whatever is_relevant / overrides say
            notes.append(Note(DeductionNote.CASH_EXCLUDED, item.id, None))
            continue
        if item.payment_method in (PaymentMethod.UNKNOWN, PaymentMethod.OTHER):
            notes.append(Note(DeductionNote.PAYMENT_UNVERIFIED, item.id, None))
        if item.labour_share_35a is None:
            notes.append(Note(DeductionNote.LABOUR_SHARE_MISSING, item.id, None))
            amount = Decimal(0)
        else:
            amount = min(item.deductible_amount, item.labour_share_35a)
        rows.append((None, item.category, amount))
    net = net_by_key(rows, notes)
    zero = Decimal(0)
    dl = _line(
        net.get((None, Category.HAUSHALTSNAHE_DIENSTLEISTUNG), zero), p.rate, p.max_dienstleistung
    )
    hw = _line(net.get((None, Category.HANDWERKERLEISTUNG), zero), p.rate, p.max_handwerker)
    return HaushaltsnaheResult(dl, hw, dl.ermaessigung + hw.ermaessigung, finish_notes(notes))
