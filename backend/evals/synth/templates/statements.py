"""Statements: Zuwendungsbestätigung, Beitragsbescheinigung, fee statement, bank statement
excerpt, Darlehenszinsbescheinigung."""

from __future__ import annotations

from decimal import Decimal

from evals.synth.templates.common import (
    MASKED_IBAN,
    Rendered,
    TemplateInput,
    dec,
)
from evals.synth.writer import Writer, date_de, eur_de, words_de


def _rows_table(w: Writer, rows: list[dict[str, str]], headers: tuple[str, str]) -> Decimal:
    total = Decimal(0)
    out = []
    for row in rows:
        amount = dec(row["amount"])
        total += amount
        out.append([str(row["text"]), eur_de(amount)])
    w.table(list(headers), out, [0.75, 0.25], ["left", "right"])
    return total


def donation_receipt(t: TemplateInput) -> Rendered:
    """Bestätigung über Zuwendungen (layout after the official model; fictional association)."""
    e = t.expected
    sach = t.get("kind") == "sach"
    amount = e.gross_amount or Decimal(0)
    w = Writer()
    w.letterhead(t.vendor, t.vendor_address, ["Vereinsregister VR 1234"])
    w.line("Aussteller (Bezeichnung und Anschrift der steuerbegünstigten Einrichtung)", size=8)
    w.line(f"{t.vendor}, {', '.join(t.vendor_address)}", size=10)
    w.gap(8)
    kind = "Sachzuwendungen" if sach else "Geldzuwendungen"
    w.line(f"Bestätigung über {kind}", size=13, bold=True)
    w.line(
        "im Sinne des § 10b des Einkommensteuergesetzes an eine der in § 5 Abs. 1 "
        "Nr. 9 KStG bezeichneten Körperschaften",
        size=8,
    )
    w.gap(6)
    w.line("Name und Anschrift des Zuwendenden:", size=8)
    for text in t.recipient_lines:
        w.line(text)
    w.gap(6)
    euros = int(abs(amount))
    w.table(
        ["Betrag in Ziffern", "in Buchstaben", "Tag der Zuwendung"],
        [
            [
                eur_de(amount),
                f"– {words_de(euros)} – Euro",
                date_de(e.payment_date) if e.payment_date else "",
            ]
        ],
        [0.3, 0.45, 0.25],
        ["left", "left", "left"],
    )
    if sach:
        w.line("Genaue Bezeichnung der Sachzuwendung mit Alter, Zustand, Kaufpreis usw.:", size=8)
        w.paragraph(str(t.get("sach_description", "")))
        w.paragraph(
            "Die Sachzuwendung stammt nach den Angaben des Zuwendenden aus dem Privatvermögen. "
            "Geeignete Unterlagen zur Wertermittlung liegen vor.",
            size=9,
        )
    else:
        w.paragraph(
            "Es handelt sich nicht um den Verzicht auf die Erstattung von Aufwendungen.", size=9
        )
    w.gap(6)
    w.paragraph(
        f"Wir sind wegen Förderung {t.get('purpose', 'des Tierschutzes')} nach dem letzten "
        "uns zugegangenen Freistellungsbescheid des Finanzamts Musterstadt von der "
        "Körperschaftsteuer befreit.",
        size=9,
    )
    w.paragraph(
        "Es wird bestätigt, dass die Zuwendung nur zur Förderung der genannten Zwecke "
        "verwendet wird.",
        size=9,
    )
    w.gap(14)
    if e.invoice_date is not None:
        w.line(f"Musterstadt, {date_de(e.invoice_date)}", size=9)
    w.line("(Unterschrift des Zuwendungsempfängers)", size=8)
    return Rendered(w.finish(), amount)


def contribution_statement(t: TemplateInput) -> Rendered:
    """Beitragsbescheinigung (insurance, union, pension contract)."""
    e = t.expected
    w = Writer()
    w.letterhead(t.vendor, t.vendor_address)
    meta = [("Mitgliedsnr.", str(t.get("member_no", "M-4711")))]
    if e.invoice_date is not None:
        meta.append(("Datum", date_de(e.invoice_date)))
    w.address_block(t.recipient_lines, meta)
    w.line(str(t.get("title", "Beitragsbescheinigung")), size=13, bold=True)
    for text in t.get("intro", []) or []:
        w.paragraph(str(text))
    w.gap(4)
    total = _rows_table(w, t.get("rows", []), ("Beitragsart", "Betrag"))
    w.pair(str(t.get("total_label", "Summe der gezahlten Beiträge")), eur_de(total), bold=True)
    for text in t.get("notes", []) or []:
        w.paragraph(str(text), size=9)
    w.gap(6)
    w.line("Diese Bescheinigung wurde maschinell erstellt und ist ohne Unterschrift gültig.", 8)
    return Rendered(w.finish(), total)


def fee_statement(t: TemplateInput) -> Rendered:
    """Kita / Tagesmutter / school fee statement, or a credit note (Gutschrift)."""
    e = t.expected
    w = Writer()
    w.letterhead(t.vendor, t.vendor_address)
    meta = []
    if t.get("number"):
        meta.append(("Belegnr.", str(t.get("number"))))
    if e.invoice_date is not None:
        meta.append(("Datum", date_de(e.invoice_date)))
    w.address_block(t.recipient_lines, meta)
    w.line(str(t.get("title", "Jahresbescheinigung über Betreuungsentgelte")), 13, bold=True)
    if t.get("child"):
        w.line(f"Kind: {t.get('child')}", size=10)
    for text in t.get("intro", []) or []:
        w.paragraph(str(text))
    w.gap(4)
    total = _rows_table(w, t.get("rows", []), ("Leistung", "Betrag"))
    w.pair(str(t.get("total_label", "Gesamtbetrag")), eur_de(total), bold=True)
    for text in t.get("notes", []) or []:
        w.paragraph(str(text), size=9)
    if e.payment_date is not None and t.get("payment_line", True):
        w.gap(4)
        w.paragraph(str(t.get("payment_line_text", "Die Beträge wurden per Lastschrift "
                                                    "von Ihrem Konto eingezogen.")), size=9)
    return Rendered(w.finish(), total)


def bank_statement(t: TemplateInput) -> Rendered:
    """Kontoauszug excerpt. Rows marked `relevant: true` add up to the labelled amount."""
    w = Writer()
    bank = str(t.get("bank", "Musterbank eG"))
    w.letterhead(bank, ["Bankplatz 3", "12345 Musterstadt"])
    w.address_block(
        t.recipient_lines,
        [("Kontoauszug", str(t.get("number", "12/2025"))), ("IBAN", MASKED_IBAN)],
    )
    w.line("Kontoauszug (Auszug)", size=13, bold=True)
    rows = []
    relevant = Decimal(0)
    for row in t.get("rows", []):
        amount = dec(row["amount"])
        if row.get("relevant"):
            relevant += amount
        rows.append([str(row["date"]), str(row["text"]), eur_de(amount, symbol=False)])
        for extra in row.get("details", []) or []:
            rows.append(["", f"  {extra}", ""])
    w.table(["Buchung", "Vorgang", "Betrag EUR"], rows, [0.15, 0.62, 0.23], ["left", "left", "right"])
    if t.get("balance"):
        w.pair("Neuer Kontostand", eur_de(dec(t.get("balance"))), size=9.5, bold=True)
    for text in t.get("notes", []) or []:
        w.paragraph(str(text), size=9)
    return Rendered(w.finish(), abs(relevant))


def loan_interest(t: TemplateInput) -> Rendered:
    """Darlehenszinsbescheinigung for a rented flat."""
    e = t.expected
    w = Writer()
    w.letterhead(t.vendor, t.vendor_address)
    meta = [("Darlehen", str(t.get("loan_no", "D-2207")))]
    if e.invoice_date is not None:
        meta.append(("Datum", date_de(e.invoice_date)))
    w.address_block(t.recipient_lines, meta)
    w.line(str(t.get("title", "Zinsbescheinigung für das Jahr 2025")), size=13, bold=True)
    w.paragraph(f"Beleihungsobjekt: {t.get('object', 'Musterweg 5, 12345 Musterstadt')}")
    w.gap(4)
    total = _rows_table(w, t.get("rows", []), ("Position", "Betrag"))
    w.pair("Summe gezahlte Zinsen", eur_de(total), bold=True)
    for text in t.get("notes", []) or []:
        w.paragraph(str(text), size=9)
    return Rendered(w.finish(), total)
