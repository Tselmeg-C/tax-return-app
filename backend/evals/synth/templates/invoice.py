"""Generic invoice with line items, and the §35a invoice with a labour / material table."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from evals.synth.templates.common import (
    MASKED_IBAN,
    Rendered,
    TemplateInput,
    dec,
    payment_text,
)
from evals.synth.writer import CENT, Writer, date_de, date_en, eur_de, eur_en


def _qty(value: Decimal) -> str:
    return f"{value.normalize():f}".replace(".", ",")


def _header(w: Writer, t: TemplateInput, title: str, number_label: str, date_label: str) -> None:
    w.letterhead(t.vendor, t.vendor_address, t.get("vendor_extra", ()))
    e = t.expected
    meta: list[tuple[str, str]] = []
    if t.get("number"):
        meta.append((number_label, str(t.get("number"))))
    if e.invoice_date is not None:
        meta.append(
            (date_label, date_en(e.invoice_date) if t.lang == "en" else date_de(e.invoice_date))
        )
    if t.get("customer_no"):
        meta.append(("Customer no." if t.lang == "en" else "Kundennr.", str(t.get("customer_no"))))
    if t.get("service_period"):
        meta.append(
            ("Service period" if t.lang == "en" else "Leistungszeitraum", str(t.get("service_period")))
        )
    w.address_block(t.recipient_lines, meta)
    w.line(title, size=13, bold=True)
    for text in t.get("intro", []) or []:
        w.paragraph(str(text))
    w.gap(4)


def _footer_block(w: Writer, t: TemplateInput) -> None:
    pay = payment_text(t)
    if pay:
        w.gap(6)
        w.paragraph(pay)
    for text in t.get("notes", []) or []:
        w.paragraph(str(text), size=9)
    if t.get("bank_line", True):
        w.gap(6)
        bank = "Bank details" if t.lang == "en" else "Bankverbindung"
        w.line(f"{bank}: Musterbank · IBAN {MASKED_IBAN}", size=8)


def _vat(total: Decimal, rate: Decimal) -> Decimal:
    return (total * rate / (100 + rate)).quantize(CENT, rounding=ROUND_HALF_UP)


def invoice(t: TemplateInput) -> Rendered:
    en = t.lang == "en"
    eur = eur_en if en else eur_de
    w = Writer(page_numbers=bool(t.get("items_per_page")))
    _header(
        w,
        t,
        str(t.get("title", "Invoice" if en else "Rechnung")),
        "Invoice no." if en else "Rechnungsnr.",
        "Date" if en else "Datum",
    )
    rows: list[list[str]] = []
    total = Decimal(0)
    for i, item in enumerate(t.get("items", []), start=1):
        qty = dec(item.get("qty", "1"))
        price = dec(item["price"])
        line_total = (qty * price).quantize(CENT)
        total += line_total
        rows.append([str(i), str(item["text"]), _qty(qty), eur(price), eur(line_total)])
    headers = (
        ["Pos.", "Description", "Qty", "Unit price", "Amount"]
        if en
        else ["Pos.", "Bezeichnung", "Menge", "Einzelpreis", "Betrag"]
    )
    per_page = t.get("items_per_page")
    widths = [0.07, 0.53, 0.1, 0.15, 0.15]
    align = ["left", "left", "right", "right", "right"]
    if per_page:
        # Multi-page: a fixed number of positions per page with a carried subtotal; the
        # total appears on the last page only.
        carried = Decimal(0)
        chunks = [rows[i : i + per_page] for i in range(0, len(rows), per_page)]
        for n, chunk in enumerate(chunks):
            if n > 0:
                w.new_page()
                w.line(f"{t.vendor} · Rechnung {t.get('number', '')} (Fortsetzung)", size=9)
                w.pair("Übertrag", eur(carried), size=9.5)
            w.table(headers, chunk, widths, align)
            carried += sum(
                (dec(it.get("qty", "1")) * dec(it["price"])).quantize(CENT)
                for it in t.get("items", [])[n * per_page : (n + 1) * per_page]
            )
            if n < len(chunks) - 1:
                w.pair("Zwischensumme (Übertrag)", eur(carried), size=9.5)
                w.paragraph(str(t.get("page_text", "")), size=9)
    else:
        w.table(headers, rows, widths, align)
    rate = dec(t.get("vat_rate", "19"))
    if rate > 0:
        label = f"incl. {rate}% VAT" if en else f"darin enthaltene USt {rate} %"
        w.pair(label, eur(_vat(total, rate)), size=9)
    elif t.get("vat_note"):
        w.line(str(t.get("vat_note")), size=9)
    w.pair("Total" if en else str(t.get("total_label", "Gesamtbetrag")), eur(total), bold=True)
    _footer_block(w, t)
    return Rendered(w.finish(), total)


_KIND_DE = {
    "labour": "Arbeitskosten",
    "travel": "Fahrtkosten",
    "machine": "Maschinenkosten",
    "material": "Material",
}


def invoice_35a(t: TemplateInput) -> Rendered:
    """Handwerker / household service invoice; prices incl. VAT; §35a statement at the end."""
    w = Writer()
    _header(w, t, str(t.get("title", "Rechnung")), "Rechnungsnr.", "Rechnungsdatum")
    rows = []
    total = Decimal(0)
    by_kind: dict[str, Decimal] = {}
    for i, item in enumerate(t.get("items", []), start=1):
        qty = dec(item.get("qty", "1"))
        price = dec(item["price"])
        line_total = (qty * price).quantize(CENT)
        kind = str(item.get("kind", "labour"))
        by_kind[kind] = by_kind.get(kind, Decimal(0)) + line_total
        total += line_total
        rows.append(
            [str(i), str(item["text"]), _KIND_DE[kind], _qty(qty), eur_de(price), eur_de(line_total)]
        )
    w.table(
        ["Pos.", "Leistung", "Art", "Menge", "Einzelpreis", "Betrag"],
        rows,
        [0.06, 0.42, 0.16, 0.08, 0.14, 0.14],
        ["left", "left", "left", "right", "right", "right"],
    )
    w.pair("darin enthaltene USt 19 %", eur_de(_vat(total, Decimal(19))), size=9)
    w.pair("Gesamtbetrag (brutto)", eur_de(total), bold=True)
    if t.get("show_35a", True):
        labour = sum(
            (v for k, v in by_kind.items() if k in ("labour", "travel", "machine")), Decimal(0)
        )
        material = by_kind.get("material", Decimal(0))
        w.gap(8)
        w.line("Ausweis für Ihre Steuererklärung (§ 35a EStG)", size=10, bold=True)
        w.pair("Arbeits-, Fahrt- und Maschinenkosten inkl. USt", eur_de(labour), size=9.5)
        w.pair("Materialkosten inkl. USt", eur_de(material), size=9.5)
    _footer_block(w, t)
    return Rendered(w.finish(), total)
