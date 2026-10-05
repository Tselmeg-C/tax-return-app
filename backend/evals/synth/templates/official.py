"""Official documents: simplified LStB printout, Jahressteuerbescheinigung, Bescheid letter."""

from __future__ import annotations

from decimal import Decimal

from evals.synth.templates.common import MASKED_STEUER_ID, Rendered, TemplateInput, dec
from evals.synth.writer import Writer, date_de, eur_de


def lstb(t: TemplateInput) -> Rendered:
    """Simplified 'Ausdruck der elektronischen Lohnsteuerbescheinigung'. Steuer-ID masked."""
    e = t.expected
    year = e.tax_year or 2025
    w = Writer()
    w.line(f"Ausdruck der elektronischen Lohnsteuerbescheinigung für {year}", 13, bold=True)
    w.line("Nachstehende Daten wurden maschinell an die Finanzverwaltung übermittelt.", 8)
    w.gap(8)
    w.address_block(
        t.recipient_lines,
        [
            ("Identifikationsnummer", MASKED_STEUER_ID),
            ("Personalnummer", str(t.get("personnel_no", "P-0815"))),
            ("Geburtsdatum", str(t.get("birth_date", "01.01.1990"))),
            ("Steuerklasse", str(t.get("tax_class", "4"))),
        ],
    )
    w.line(f"Arbeitgeber: {t.vendor}, {', '.join(t.vendor_address)}", size=9)
    w.gap(6)
    rows = []
    for row in t.get("rows", []):
        rows.append([str(row["nr"]), str(row["text"]), eur_de(dec(row["amount"]))])
    w.table(["Zeile", "Bezeichnung", "Betrag"], rows, [0.1, 0.65, 0.25], ["left", "left", "right"])
    if e.invoice_date is not None:
        w.line(f"Erstellt am {date_de(e.invoice_date)}", size=8)
    return Rendered(w.finish(), None)


def jstb(t: TemplateInput) -> Rendered:
    """Simplified Jahressteuerbescheinigung (Kapitalerträge). The row marked `total` is the gross."""
    e = t.expected
    w = Writer()
    w.letterhead(t.vendor, t.vendor_address)
    meta = [("Depot", str(t.get("depot_no", "DP-3301")))]
    if e.invoice_date is not None:
        meta.append(("Datum", date_de(e.invoice_date)))
    w.address_block(t.recipient_lines, meta)
    w.line(f"Jahressteuerbescheinigung {e.tax_year or 2025}", size=13, bold=True)
    w.line("für Privatkonten und/oder Privatdepots", size=9)
    w.gap(4)
    rows = []
    total = Decimal(0)
    for row in t.get("rows", []):
        amount = dec(row["amount"])
        if row.get("total"):
            total = amount
        rows.append([str(row["text"]), eur_de(amount)])
    w.table(["Angabe", "Betrag"], rows, [0.72, 0.28], ["left", "right"])
    for text in t.get("notes", []) or []:
        w.paragraph(str(text), size=9)
    return Rendered(w.finish(), total)


def bescheid(t: TemplateInput) -> Rendered:
    """Bescheid letter (Familienkasse / Elterngeldstelle)."""
    e = t.expected
    w = Writer()
    w.letterhead(t.vendor, t.vendor_address)
    meta = [("Aktenzeichen", str(t.get("file_no", "AZ 12-345")))]
    if e.invoice_date is not None:
        meta.append(("Datum", date_de(e.invoice_date)))
    w.address_block(t.recipient_lines, meta)
    w.line(str(t.get("title", "Bescheid")), size=13, bold=True)
    for text in t.get("paragraphs", []) or []:
        w.paragraph(str(text))
        w.gap(3)
    rows = []
    for row in t.get("rows", []) or []:
        rows.append([str(row["text"]), eur_de(dec(row["amount"]))])
    if rows:
        w.table(["Zeitraum / Leistung", "Betrag"], rows, [0.72, 0.28], ["left", "right"])
    w.gap(6)
    w.line("Rechtsbehelfsbelehrung", size=10, bold=True)
    w.paragraph(
        "Gegen diesen Bescheid kann innerhalb eines Monats nach Bekanntgabe Einspruch "
        "erhoben werden.",
        size=9,
    )
    return Rendered(w.finish(), None)
