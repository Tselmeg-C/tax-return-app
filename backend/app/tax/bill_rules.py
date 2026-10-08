"""Bill rules (#9 Decision 3): what the LLM read → one tax item draft + attention reasons.

Pure (no I/O). The LLM only reads amounts, dates and a category per line; deductible
amount, §35a labour share, cash rule, AfA, tax year (§11 Abfluss), Anlage / Zeile and the
`needs_attention` reasons are decided here. Golden tests: `tests/tax/test_bill_rules.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from app.domain.enums import (
    CATEGORY_GROUP,
    Anlage,
    AttentionReason,
    Category,
    CategoryGroup,
    DocType,
    PaymentMethod,
)
from app.pipeline.schemas import (
    CONFIDENCE_VALUE,
    ClassifyOutput,
    Confidence,
    CostKind35a,
    GenericBillExtraction,
    LineItem,
    money,
)
from app.tax.mapping import MappingTable, map_category

R = AttentionReason
ZERO = Decimal("0.00")
CENT = Decimal("0.01")
SUM_TOLERANCE = Decimal("0.02")
MAX_PLAUSIBLE = Decimal("100000.00")
EARLIEST = date(2000, 1, 1)
GWG_NET_LIMIT = Decimal("800.00")  # § 6 Abs. 2 EStG (since 2018)
# ponytail: one useful life (office furniture, AfA-Tabelle AV 6.6) for every asset above the
# GWG limit, flagged `asset_depreciation`; a per-asset life and the later years' AfA are #15.
AFA_YEARS = Decimal(13)
DEFAULT_VAT = Decimal("0.19")

OFFICIAL_DOC_TYPES: frozenset[DocType] = frozenset(
    {
        DocType.LOHNSTEUERBESCHEINIGUNG,
        DocType.JAHRESSTEUERBESCHEINIGUNG,
        DocType.NEBENKOSTENABRECHNUNG,
        DocType.KINDERGELD_BESCHEID,
        DocType.ELTERNGELD_BESCHEID,
        DocType.ALG_BESCHEID,
    }
)
LABOUR_KINDS = frozenset({CostKind35a.LABOUR, CostKind35a.TRAVEL, CostKind35a.MACHINE})
ASSET_CATEGORIES = frozenset({Category.WK_ARBEITSMITTEL, Category.WK_ARBEITSZIMMER})
RECURRING: frozenset[Category] = frozenset(
    {
        Category.KINDERBETREUUNG,
        Category.SCHULGELD,
        Category.WK_BERUFSVERBAND,
        Category.V_SCHULDZINSEN,
        Category.KIRCHENSTEUER,
    }
    | {c for c, g in CATEGORY_GROUP.items() if g is CategoryGroup.VORSORGE}
)


@dataclass(frozen=True)
class TaxItemDraft:
    category: Category
    is_relevant: bool
    gross_amount: Decimal
    deductible_amount: Decimal
    labour_share_35a: Decimal | None
    vendor: str | None
    recipient_name: str | None  # for person matching only; never stored or logged
    invoice_date: date | None
    payment_date: date | None
    payment_method: PaymentMethod
    tax_year: int | None
    anlage: Anlage | None
    zeile: str | None
    reason: str | None
    confidence: Decimal


@dataclass(frozen=True)
class RulesResult:
    draft: TaxItemDraft | None
    reasons: list[AttentionReason] = field(default_factory=list)

    @property
    def stored_reason(self) -> AttentionReason | None:
        return self.reasons[0] if self.reasons else None


EXTRACTABLE = frozenset({DocType.GENERIC_BILL, DocType.OTHER})


def needs_extract(c: ClassifyOutput) -> bool:
    """Extract runs for a readable, single, relevant bill. `other` marked relevant is
    extracted too (v2): a classify slip must not silently drop a deduction; the extract
    lines decide (all `irrelevant` → an irrelevant item)."""
    return c.doc_type in EXTRACTABLE and c.tax_relevant and c.readable and not c.multiple_documents


def ordered(reasons: set[AttentionReason]) -> list[AttentionReason]:
    """Priority order = definition order of `AttentionReason`."""
    return [r for r in AttentionReason if r in reasons]


def _sum(lines: list[LineItem]) -> Decimal:
    return sum((money(line.gross_amount) for line in lines), ZERO)


def _year(
    payment: date | None, invoice: date | None, fallback: int | None
) -> tuple[int | None, bool]:
    """(tax year, dates_missing). §11: payment year, else invoice year, else the fallback."""
    for d in (payment, invoice):
        if d is not None and 2000 <= d.year <= 2100:
            return d.year, False
    return fallback, payment is None and invoice is None


def _year_boundary(invoice: date | None, payment: date | None) -> bool:
    """Paid 22 Dec – 10 Jan, invoiced in the other year (10-Tage-Regel: a human decides)."""
    if invoice is None or payment is None or invoice.year == payment.year:
        return False
    day = (payment.month, payment.day)
    return day >= (12, 22) or day <= (1, 10)


def _afa(amount: Decimal, acquired: date | None, year: int | None) -> Decimal:
    """Linear AfA share for the year, pro rata by month from the month of acquisition."""
    months = 12
    if acquired is not None and year is not None and acquired.year == year:
        months = 13 - acquired.month
    return (amount / AFA_YEARS * months / 12).quantize(CENT, rounding=ROUND_HALF_UP)


def _irrelevant(
    c: ClassifyOutput,
    e: GenericBillExtraction | None,
    fallback_year: int | None,
    reasons: set[AttentionReason],
) -> RulesResult:
    if e is None:
        gross = money(c.total_gross) if c.total_gross is not None else ZERO
        invoice, payment, method, vendor, recipient = (
            c.document_date,
            None,
            PaymentMethod.UNKNOWN,
            c.vendor,
            None,
        )
        reason, confidence = c.reason_de, c.confidence
    else:
        gross = money(e.total_gross)
        invoice, payment, method = e.invoice_date, e.payment_date, e.payment_method
        vendor, recipient = e.vendor or c.vendor, e.recipient_name
        reason, confidence = e.reason_de, e.confidence
    year, _ = _year(payment, invoice, fallback_year)
    draft = TaxItemDraft(
        category=Category.IRRELEVANT,
        is_relevant=False,
        gross_amount=gross,
        deductible_amount=ZERO,
        labour_share_35a=None,
        vendor=vendor,
        recipient_name=recipient,
        invoice_date=invoice,
        payment_date=payment,
        payment_method=method,
        tax_year=year,
        anlage=None,
        zeile=None,
        reason=reason,
        confidence=CONFIDENCE_VALUE[confidence],
    )
    return RulesResult(draft, ordered(reasons))


def evaluate(
    c: ClassifyOutput,
    e: GenericBillExtraction | None,
    mapping: MappingTable,
    today: date,
    fallback_year: int | None = None,
) -> RulesResult:
    """`fallback_year`: the upload year (Europe/Berlin) the handler uses when no date is
    printed; the eval passes None (tax year unknown)."""
    reasons: set[AttentionReason] = set()
    if c.confidence is Confidence.LOW or (e is not None and e.confidence is Confidence.LOW):
        reasons.add(R.LOW_CONFIDENCE)
    if not c.readable:
        return RulesResult(None, [R.UNREADABLE])
    if c.multiple_documents:
        return RulesResult(None, [R.MULTIPLE_DOCUMENTS])
    if c.doc_type in OFFICIAL_DOC_TYPES:
        return RulesResult(None, [R.DOC_TYPE_NOT_SUPPORTED])
    if e is None or not needs_extract(c):
        return _irrelevant(c, None, fallback_year, reasons)

    relevant = [line for line in e.line_items if line.category is not Category.IRRELEVANT]
    if not relevant:
        return _irrelevant(c, e, fallback_year, reasons)

    totals: dict[Category, Decimal] = {}
    for line in relevant:
        totals[line.category] = totals.get(line.category, ZERO) + money(line.gross_amount)
    primary = max(totals, key=lambda cat: abs(totals[cat]))  # ties: first printed
    if len(totals) > 1:
        reasons.add(R.MULTIPLE_CATEGORIES)
    lines = [line for line in relevant if line.category is primary]
    gross = money(e.total_gross)
    deductible = _sum(lines)
    labour: Decimal | None = None
    is_relevant = True

    if primary is Category.BEHINDERUNG:
        # § 33 Abs. 2a EStG: disability-related travel only via the Pauschale (profile).
        other = [line for line in lines if line.cost_kind_35a is not CostKind35a.TRAVEL]
        deductible = _sum(other)
        is_relevant = bool(other)

    year, dates_missing = _year(e.payment_date, e.invoice_date, fallback_year)

    if CATEGORY_GROUP[primary] is CategoryGroup.HAUSHALTSNAHE:
        if e.stated_labour_amount_35a is not None:
            labour = money(e.stated_labour_amount_35a)
        elif any(line.cost_kind_35a in LABOUR_KINDS for line in lines):
            labour = _sum([line for line in lines if line.cost_kind_35a in LABOUR_KINDS])
        if labour is None:
            reasons.add(R.LABOUR_SHARE_MISSING)
            deductible = ZERO
        elif labour < 0 or deductible < 0:
            reasons.add(R.CREDIT_NOTE_35A)
            labour, deductible = None, ZERO
        else:
            deductible = labour
        if e.payment_method is PaymentMethod.CASH:
            is_relevant = False  # § 35a Abs. 5: cashless payment only
        elif e.payment_method is PaymentMethod.UNKNOWN:
            reasons.add(R.PAYMENT_METHOD_UNKNOWN_35A)

    if primary in ASSET_CATEGORIES and deductible > 0:
        vat_share = (
            money(e.total_vat) / gross
            if e.total_vat is not None and gross > 0
            else DEFAULT_VAT / (1 + DEFAULT_VAT)
        )
        net = deductible * (1 - vat_share)
        if net > GWG_NET_LIMIT:
            deductible = _afa(deductible, e.invoice_date or e.payment_date, year)
            reasons.add(R.ASSET_DEPRECIATION)

    if abs(_sum(list(e.line_items)) - gross) > SUM_TOLERANCE:
        reasons.add(R.SUM_MISMATCH)
    if e.is_credit_note != (gross < 0):
        reasons.add(R.SIGN_MISMATCH)
    if abs(gross) > MAX_PLAUSIBLE:
        reasons.add(R.IMPLAUSIBLE_AMOUNT)
    dates = [d for d in (e.invoice_date, e.payment_date, c.document_date) if d is not None]
    if any(d > today or d < EARLIEST for d in dates):
        reasons.add(R.IMPLAUSIBLE_DATE)
    if e.currency != "EUR":
        reasons.add(R.FOREIGN_CURRENCY)
        deductible = ZERO
    if dates_missing:
        reasons.add(R.DATE_MISSING)
    if primary in RECURRING and _year_boundary(e.invoice_date, e.payment_date):
        reasons.add(R.YEAR_BOUNDARY_RECURRING)

    anlage = zeile = None
    if year is not None:
        form = map_category(primary, year, mapping)
        anlage, zeile = form.anlage, form.zeile
        if not form.supported:
            reasons.add(R.UNSUPPORTED_YEAR)

    if not is_relevant:
        deductible = ZERO
    draft = TaxItemDraft(
        category=primary,
        is_relevant=is_relevant,
        gross_amount=gross,
        deductible_amount=deductible,
        labour_share_35a=labour,
        vendor=e.vendor or c.vendor,
        recipient_name=e.recipient_name,
        invoice_date=e.invoice_date,
        payment_date=e.payment_date,
        payment_method=e.payment_method,
        tax_year=year,
        anlage=anlage,
        zeile=zeile,
        reason=e.reason_de,
        confidence=CONFIDENCE_VALUE[e.confidence],
    )
    return RulesResult(draft, ordered(reasons))
