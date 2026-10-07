"""Shared inputs and text snippets for the document templates."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.domain.enums import PaymentMethod
from evals.schema import ExpectedFields
from evals.synth.writer import HOUSEHOLD_ADDRESS, date_de, date_en


@dataclass(frozen=True)
class TemplateInput:
    case_id: str
    doc: dict[str, Any]
    expected: ExpectedFields
    seed: int

    def get(self, key: str, default: Any = None) -> Any:
        return self.doc.get(key, default)

    @property
    def lang(self) -> str:
        return str(self.doc.get("lang", "de"))

    @property
    def vendor(self) -> str:
        return str(self.doc.get("vendor") or self.expected.vendor or "Beispiel GmbH")

    @property
    def vendor_address(self) -> list[str]:
        addr = self.doc.get("vendor_address")
        if addr:
            return [str(a) for a in addr]
        return [f"Beispielweg {self.seed % 40 + 2}", "12345 Musterstadt"]

    @property
    def recipient_lines(self) -> list[str]:
        name = self.doc.get("recipient", self.expected.person_hint)
        if not name:
            return []
        addr = self.doc.get("recipient_address")
        return [str(name), *(addr or HOUSEHOLD_ADDRESS)]


@dataclass(frozen=True)
class Rendered:
    pdf: bytes
    total: Decimal | None
    """The amount the document prints as its total, checked against `expected.gross_amount`."""


Template = Callable[[TemplateInput], Rendered]


def dec(value: Any) -> Decimal:
    if isinstance(value, float):
        raise TypeError("spec amounts must be strings")
    return Decimal(str(value))


def payment_text(t: TemplateInput) -> str | None:
    """Payment line printed on invoices, derived from the label unless the spec overrides it."""
    if "payment_text" in t.doc:
        text = t.doc["payment_text"]
        return None if text is None else str(text)
    e = t.expected
    method = e.payment_method
    if t.lang == "en":
        if e.payment_date is None:
            return "Payment due within 14 days." if method is PaymentMethod.BANK_TRANSFER else None
        how = {
            PaymentMethod.CARD: "by card",
            PaymentMethod.PAYPAL: "via PayPal",
            PaymentMethod.BANK_TRANSFER: "by bank transfer",
        }.get(method, "")
        return f"Paid {how} on {date_en(e.payment_date)}. Thank you!".replace("  ", " ")
    if e.payment_date is None:
        if method is PaymentMethod.BANK_TRANSFER:
            return (
                "Bitte überweisen Sie den Rechnungsbetrag innerhalb von 14 Tagen "
                "auf das unten genannte Konto."
            )
        return None
    d = date_de(e.payment_date)
    return {
        PaymentMethod.CASH: f"Betrag bar erhalten am {d}. Vielen Dank!",
        PaymentMethod.BANK_TRANSFER: f"Zahlungseingang per Überweisung am {d}. Vielen Dank!",
        PaymentMethod.DIRECT_DEBIT: f"Der Betrag wurde am {d} per Lastschrift eingezogen.",
        PaymentMethod.CARD: f"Bezahlt mit Karte am {d}.",
        PaymentMethod.PAYPAL: f"Bezahlt per PayPal am {d}.",
        PaymentMethod.OTHER: f"Bezahlt am {d}.",
        PaymentMethod.UNKNOWN: f"Bezahlt am {d}.",
    }[method]


MASKED_IBAN = "DE00 XXXX XXXX XXXX XXXX 00"
MASKED_STEUER_ID = "XX XXX XXX XXX"
