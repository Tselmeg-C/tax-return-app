"""`heuristic`: the trivial baseline required by `_docs/adlc.md`.

Keyword and regex rules over the PDF text layer (pypdf). Images and scanned PDFs have no
text, so it predicts `tax_relevant: false`, `category: irrelevant`. Deterministic, offline,
`calls: []`.
"""

from __future__ import annotations

import io
import re
from datetime import date
from decimal import Decimal

from pypdf import PdfReader

from app.domain.enums import CATEGORY_GROUP, Category, CategoryGroup, DocType, PaymentMethod
from evals.dataset import EvalCase
from evals.predictors.base import PredictorOptions
from evals.schema import Prediction

_C = Category

# First match wins; keywords are matched against the lower-cased text.
CATEGORY_RULES: tuple[tuple[tuple[str, ...], Category], ...] = (
    (("zuwendung", "spende"), _C.SPENDEN),
    (("kirchensteuer nachzahlung",), _C.KIRCHENSTEUER),
    (("schulgeld",), _C.SCHULGELD),
    (("betreuungsentgelt", "kindertagespflege", "kita "), _C.KINDERBETREUUNG),
    (("§ 92 estg", "altersvorsorgevertrag"), _C.VORSORGE_RIESTER),
    (("basisrente",), _C.VORSORGE_RUERUP),
    (("krankenversicherung", "pflegeversicherung"), _C.VORSORGE_KV_PV),
    (("rentenversicherung",), _C.VORSORGE_RV),
    (("haftpflicht",), _C.VORSORGE_SONSTIGE),
    (("pflegeleistungen", "pflegedienst"), _C.PFLEGE),
    (("behinderung",), _C.BEHINDERUNG),
    (("zahnarzt", "verordnung", "rezept", "apotheke"), _C.KRANKHEITSKOSTEN),
    (
        ("reinigung", "haushaltshilfe", "winterdienst", "gartenservice"),
        _C.HAUSHALTSNAHE_DIENSTLEISTUNG,
    ),
    (("§ 35a", "arbeitskosten"), _C.HANDWERKERLEISTUNG),
    (("sollzinsen", "zinsbescheinigung"), _C.V_SCHULDZINSEN),
    (("grundsteuer", "gebäudeversicherung"), _C.V_NEBENKOSTEN),
    (("vermietete wohnung",), _C.V_ERHALTUNG),
    (("gewerkschaft", "berufsverband"), _C.WK_BERUFSVERBAND),
    (("kontoführung",), _C.WK_KONTOFUEHRUNG),
    (("steuerberatung",), _C.STEUERBERATUNG),
    (("seminar", "fortbildung", "course"), _C.WK_FORTBILDUNG),
    (("bewerbung",), _C.WK_BEWERBUNG),
    (("dienstreise",), _C.WK_FAHRTKOSTEN),
    (("arbeitszimmer",), _C.WK_ARBEITSZIMMER),
    (("notebook", "laptop"), _C.WK_ARBEITSMITTEL),
)

TOTAL_KEYWORDS = (
    "gesamtbetrag",
    "summe",
    "total",
    "jahresbetrag",
    "jahresbeitrag",
    "gutschriftbetrag",
    "kapitalerträge",
)
LABOUR_KEYWORD = "arbeits-, fahrt- und maschinenkosten"
PAYMENT_KEYWORDS = ("bezahlt", "zahlungseingang", "erhalten am", "eingezogen", "zuletzt am")

_AMOUNT_RE = re.compile(r"-?\d{1,3}(?:\.\d{3})*,\d{2}")
_DATE_RE = re.compile(r"\b(\d{2})\.(\d{2})\.(\d{4})\b")


def _amount(text: str) -> Decimal:
    return Decimal(text.replace(".", "").replace(",", "."))


def _last_amount(line: str) -> Decimal | None:
    found = _AMOUNT_RE.findall(line)
    return _amount(found[-1]) if found else None


def _date(line: str) -> date | None:
    m = _DATE_RE.search(line)
    if not m:
        return None
    day, month, year = (int(g) for g in m.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


def pdf_text(data: bytes) -> str:
    reader = PdfReader(io.BytesIO(data))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def predict_from_text(text: str) -> Prediction:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    low = text.lower()
    if not lines:
        return Prediction(
            doc_type=DocType.GENERIC_BILL,
            tax_relevant=False,
            category=Category.IRRELEVANT,
            deductible_amount=Decimal("0.00"),
        )

    doc_type = DocType.GENERIC_BILL
    category: Category | None = Category.IRRELEVANT
    if "lohnsteuerbescheinigung" in low:
        doc_type, category = DocType.LOHNSTEUERBESCHEINIGUNG, None
    elif "jahressteuerbescheinigung" in low:
        doc_type, category = DocType.JAHRESSTEUERBESCHEINIGUNG, Category.KAPITAL_BESCHEINIGUNG
    elif "kindergeld" in low and "bescheid" in low:
        doc_type, category = DocType.KINDERGELD_BESCHEID, None
    elif "elterngeld" in low and "bescheid" in low:
        doc_type, category = DocType.ELTERNGELD_BESCHEID, None
    else:
        for keywords, cat in CATEGORY_RULES:
            if any(k in low for k in keywords):
                category = cat
                break
    relevant = category is not Category.IRRELEVANT

    gross: Decimal | None = None
    labour: Decimal | None = None
    invoice_date: date | None = None
    payment_date: date | None = None
    for i, line in enumerate(lines):
        ll = line.lower()
        # pypdf often puts a label and its value on separate lines: look one line ahead.
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if any(k in ll for k in TOTAL_KEYWORDS) and "zwischensumme" not in ll:
            found = _last_amount(line)
            if found is None:
                found = _last_amount(nxt)
            if found is not None:
                gross = found
        if LABOUR_KEYWORD in ll:
            labour = _last_amount(line)
            if labour is None:
                labour = _last_amount(nxt)
        if invoice_date is None and "datum" in ll:
            invoice_date = _date(line) or _date(nxt)
        if any(k in ll for k in PAYMENT_KEYWORDS):
            payment_date = _date(line) or payment_date
    if invoice_date is None:
        invoice_date = next((d for d in map(_date, lines) if d is not None), None)

    if doc_type is not DocType.GENERIC_BILL and doc_type is not (DocType.JAHRESSTEUERBESCHEINIGUNG):
        gross = None
    deductible: Decimal | None
    if not relevant or category in (None, Category.KAPITAL_BESCHEINIGUNG):
        deductible = Decimal("0.00")
    elif category is not None and CATEGORY_GROUP[category] is CategoryGroup.HAUSHALTSNAHE:
        deductible = labour if labour is not None else gross
    else:
        deductible = gross
    if category is None or CATEGORY_GROUP[category] is not CategoryGroup.HAUSHALTSNAHE:
        labour = None

    method = PaymentMethod.UNKNOWN
    for keys, pm in (
        (("lastschrift",), PaymentMethod.DIRECT_DEBIT),
        (("überweis", "dauerauftrag"), PaymentMethod.BANK_TRANSFER),
        (("paypal",), PaymentMethod.PAYPAL),
        (("karte", "girocard", "card"), PaymentMethod.CARD),
        (("bar ", "\nbar", "bar erhalten"), PaymentMethod.CASH),
    ):
        if any(k in low for k in keys):
            method = pm
            break

    when = payment_date or invoice_date
    return Prediction(
        doc_type=doc_type,
        tax_relevant=relevant,
        category=category,
        gross_amount=gross,
        deductible_amount=deductible,
        labour_share_35a=labour,
        invoice_date=invoice_date,
        payment_date=payment_date,
        tax_year=when.year if when is not None and when.year in (2025, 2026) else None,
        payment_method=method,
        vendor=lines[0],
        person_hint=None,
    )


class HeuristicPredictor:
    name = "heuristic"

    def describe(self) -> dict[str, str]:
        return {
            "name": self.name,
            "provider": "none",
            "model": "heuristic-keywords",
            "prompt_version": "-",
            "version": "1",
        }

    async def predict(self, case: EvalCase) -> Prediction:
        if case.mime_type != "application/pdf":
            return predict_from_text("")
        return predict_from_text(pdf_text(case.read_bytes()))


def make_heuristic(options: PredictorOptions) -> HeuristicPredictor:
    del options
    return HeuristicPredictor()
