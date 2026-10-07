"""Narrow till receipt (Kassenbon)."""

from __future__ import annotations

from decimal import Decimal

from reportlab.lib.units import mm

from app.domain.enums import PaymentMethod
from evals.synth.templates.common import Rendered, TemplateInput, dec
from evals.synth.writer import Writer, date_de, eur_de


def receipt(t: TemplateInput) -> Rendered:
    items = t.get("items", [])
    extra = len(t.get("notes", []) or []) + len(t.get("header_lines", []) or [])
    height = (95 + 5.2 * (len(items) + extra)) * mm
    w = Writer(pagesize=(80 * mm, height), margin=6 * mm, footer_size=5.2)
    w.line(t.vendor, size=11, bold=True, align="center")
    for text in t.vendor_address:
        w.line(text, size=7.5, align="center")
    for text in t.get("header_lines", []) or []:
        w.line(str(text), size=7.5, align="center")
    w.gap(4)
    e = t.expected
    when = e.payment_date or e.invoice_date
    if when is not None:
        w.pair(date_de(when), str(t.get("time", "10:42")), size=8)
    if t.get("number"):
        w.pair("Bon-Nr.", str(t.get("number")), size=8)
    w.rule()
    total = Decimal(0)
    for item in items:
        price = dec(item["price"])
        qty = dec(item.get("qty", "1"))
        line_total = qty * price
        total += line_total
        text = str(item["text"])
        if qty != 1:
            text = f"{qty.normalize():f} x {text}"
        marker = f" {item['marker']}" if item.get("marker") else ""
        w.pair(text[:30], eur_de(line_total, symbol=False) + marker, size=8)
    w.rule()
    w.pair("SUMME EUR", eur_de(total, symbol=False), size=10, bold=True)
    method = e.payment_method
    if method is PaymentMethod.CASH:
        given = dec(t.get("given", str(total)))
        w.pair("BAR", eur_de(given, symbol=False), size=8)
        w.pair("Rückgeld", eur_de(given - total, symbol=False), size=8)
    elif method is PaymentMethod.CARD:
        w.pair("Kartenzahlung girocard", eur_de(total, symbol=False), size=8)
    for text in t.get("notes", []) or []:
        w.line(str(text), size=7)
    w.gap(4)
    w.line("Vielen Dank für Ihren Einkauf!", size=7.5, align="center")
    return Rendered(w.finish(), total)
