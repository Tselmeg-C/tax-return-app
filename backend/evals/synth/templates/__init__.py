"""Document templates: each is a small function drawing one document type with reportlab."""

from __future__ import annotations

from evals.synth.templates.common import Rendered, Template, TemplateInput
from evals.synth.templates.invoice import invoice, invoice_35a
from evals.synth.templates.official import bescheid, jstb, lstb
from evals.synth.templates.receipt import receipt
from evals.synth.templates.statements import (
    bank_statement,
    contribution_statement,
    donation_receipt,
    fee_statement,
    loan_interest,
)

TEMPLATES: dict[str, Template] = {
    "invoice": invoice,
    "invoice_35a": invoice_35a,
    "receipt": receipt,
    "donation_receipt": donation_receipt,
    "contribution_statement": contribution_statement,
    "fee_statement": fee_statement,
    "bank_statement": bank_statement,
    "loan_interest": loan_interest,
    "lstb": lstb,
    "jstb": jstb,
    "bescheid": bescheid,
}

__all__ = ["TEMPLATES", "Rendered", "Template", "TemplateInput"]
