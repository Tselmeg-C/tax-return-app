"""The PDF report (reportlab Platypus, A4, built-in Helvetica, deterministic via `invariant`).

Renders `ExportData` only: every figure is a string of the summary, formatted, never computed.
Text goes through `_t` (WinAnsi-safe, XML-escaped) because Helvetica has no glyphs beyond
cp1252 and Paragraph parses markup.
"""

from __future__ import annotations

import io
from datetime import date
from decimal import Decimal
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    Flowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.domain.enums import (
    LABELS_DE,
    Anlage,
    Category,
    DeductionNote,
    EstimateMissing,
    FilingStatus,
    PaymentMethod,
    PflichtCheck,
    PflichtDeadlineKind,
    PflichtReason,
    PflichtState,
)
from app.export.data import VENDOR_MAX, ExportData, Group, Line, de_date, de_money, zeile_key

DISCLAIMER = (
    "Dies ist eine unverbindliche Schätzung und Belegübersicht, keine Steuerberatung und keine "
    "Steuererklärung. Alle Werte müssen vor der Abgabe geprüft werden; maßgeblich ist der "
    "Bescheid des Finanzamts. Werte mit 'vorläufig' sind nicht vollständig gegen den "
    "Gesetzestext bestätigt."
)
DISCLAIMER_SHORT = "Unverbindliche Schätzung, keine Steuerberatung. Maßgeblich ist der Bescheid."
KAP_V_TEXT = (
    "Kapitalerträge und Vermietung (Anlage KAP / V) sind in dieser Version nicht berechnet; "
    "Belege dazu stehen in der Liste, fließen aber nicht in die Schätzung."
)
VORSORGE_ESTIMATE_NOTES = frozenset(
    {
        DeductionNote.EMPLOYER_SHARE_ASSUMED,
        DeductionNote.KV_PV_UNSPLIT,
        DeductionNote.VORSORGE_ESTIMATED,
    }
)
SECTION_LABELS = {
    "church_tax": "Kirchensteuer",
    "progressionsvorbehalt": "Progressionsvorbehalt",
    "kinder": "Kinder",
    "entlastungsbetrag": "Entlastungsbetrag",
    "sozialversicherung": "Sozialversicherung",
}
GREY = colors.HexColor("#555555")
LIGHT = colors.HexColor("#eeeeee")
MARGIN = 20 * mm
WIDTH = A4[0] - 2 * MARGIN


def _t(text: str | None) -> str:
    """cp1252-safe (other characters become `?`) and XML-escaped."""
    return escape((text or "").encode("cp1252", errors="replace").decode("cp1252"))


def _eur(value: str | Decimal) -> str:
    return f"{de_money(value)} €"


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()["Normal"]
    return {
        "p": ParagraphStyle("p", parent=base, fontName="Helvetica", fontSize=9, leading=12),
        "small": ParagraphStyle("small", parent=base, fontSize=7.5, leading=9.5, textColor=GREY),
        "right": ParagraphStyle("right", parent=base, fontSize=9, leading=12, alignment=TA_RIGHT),
        "bold": ParagraphStyle("bold", parent=base, fontName="Helvetica-Bold", fontSize=9),
        "boldr": ParagraphStyle(
            "boldr", parent=base, fontName="Helvetica-Bold", fontSize=9, alignment=TA_RIGHT
        ),
        "h1": ParagraphStyle("h1", parent=base, fontName="Helvetica-Bold", fontSize=16, leading=20),
        "h2": ParagraphStyle(
            "h2",
            parent=base,
            fontName="Helvetica-Bold",
            fontSize=12,
            leading=15,
            spaceBefore=10,
            keepWithNext=1,
        ),
        "anlage": ParagraphStyle(
            "anlage", parent=base, fontName="Helvetica-Bold", fontSize=9, keepWithNext=1
        ),
        "box": ParagraphStyle("box", parent=base, fontSize=8.5, leading=11),
    }


class _Numbered(Canvas):
    """Footer with `Seite x von y` (two-pass: the page count is known at the end)."""

    def __init__(self, year: int, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.year = year
        self._saved: list[dict[str, Any]] = []

    def showPage(self) -> None:  # noqa: N802 (reportlab API)
        self._saved.append(dict(self.__dict__))
        self._startPage()  # type: ignore[attr-defined]

    def save(self) -> None:
        total = len(self._saved)
        for state in self._saved:
            self.__dict__.update(state)
            self.setFont("Helvetica", 7.5)
            self.setFillColor(GREY)
            self.drawString(
                MARGIN,
                11 * mm,
                _plain(
                    f"belegbot · Steuerjahr {self.year} · Seite {self.getPageNumber()} von {total}"
                ),
            )
            self.drawString(MARGIN, 7.5 * mm, _plain(DISCLAIMER_SHORT))
            super().showPage()
        super().save()


def _plain(text: str) -> str:
    return text.encode("cp1252", errors="replace").decode("cp1252")


def _note_text(code: str, item_id: str | None, person_id: str | None, data: ExportData) -> str:
    text = LABELS_DE[DeductionNote][DeductionNote(code)]
    refs = []
    if item_id:
        refs.append(f"Beleg-Nr. {item_id[:8]}")
    if person_id:
        refs.append(data.roles.get(person_id, "weitere Person"))
    return f"{text} ({', '.join(refs)})" if refs else text


def _table(rows: list[list[Any]], widths: list[float], extra: list[Any] | None = None) -> Table:
    table = Table(rows, colWidths=widths, repeatRows=0)
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                *(extra or []),
            ]
        )
    )
    return table


def _estimate_rows(s: dict[str, Any], st: dict[str, ParagraphStyle]) -> list[list[Any]]:
    rows: list[tuple[str, str, bool]] = [
        ("Gesamtbetrag der Einkünfte", s["gde"], False),
        ("Zu versteuerndes Einkommen", s["zve"], False),
        ("Tarifliche Einkommensteuer", s["tarifliche_est"], False),
        ("Ermäßigung nach § 35a", s["ermaessigung_35a"], False),
        ("Festzusetzende Einkommensteuer", s["festzusetzende_est"], False),
        ("Solidaritätszuschlag", s["soli"], False),
        ("Kirchensteuer", s["kirchensteuer"], False),
        ("Einbehaltene Lohnsteuer", s["withheld"]["lohnsteuer"], False),
        ("Einbehaltener Solidaritätszuschlag", s["withheld"]["soli"], False),
        ("Einbehaltene Kirchensteuer", s["withheld"]["kirchensteuer"], False),
    ]
    refund = Decimal(s["refund"])
    word = "Nachzahlung" if refund < 0 else "Erstattung"
    out = [[Paragraph(_t(label), st["p"]), Paragraph(_eur(v), st["right"])] for label, v, _ in rows]
    out.append([Paragraph(_t(word), st["bold"]), Paragraph(_eur(abs(refund)), st["boldr"])])
    out.append(
        [
            Paragraph(
                _t("Kindergeld (zusätzlich zur Steuer, nicht in der Erstattung)"), st["small"]
            ),
            Paragraph(_eur(s["kindergeld_added"]), st["right"]),
        ]
    )
    return out


def _flags(data: ExportData) -> list[str]:
    s = data.summary
    flags = []
    if s["provisional"]:
        names = (SECTION_LABELS.get(n, n) for n in s["provisional_sections"])
        flags.append(
            "vorläufig (nicht vollständig gegen den Gesetzestext bestätigt): " + ", ".join(names)
        )
    notes = {n["code"] for n in s["notes"]}
    if any(c.value in notes for c in VORSORGE_ESTIMATE_NOTES) or any(
        m["code"] == EstimateMissing.VORSORGE_MISSING for m in s["what_missing"]
    ):
        flags.append("Vorsorge geschätzt")
    return flags


def _pflicht(data: ExportData) -> list[str]:
    p = data.summary["pflicht"]
    lines = [LABELS_DE[PflichtState][PflichtState(p["state"])]]
    lines += [LABELS_DE[PflichtReason][PflichtReason(r)] for r in p["reasons"]]
    kind = LABELS_DE[PflichtDeadlineKind][PflichtDeadlineKind(p["deadline_kind"])]
    deadline = f"{kind}: {de_date(date.fromisoformat(p['deadline']))}"
    lines.append(deadline + (" (Frist abgelaufen)" if p["overdue"] else ""))
    return lines


def _head(data: ExportData, st: dict[str, ParagraphStyle]) -> list[Flowable]:
    s = data.summary
    filing = LABELS_DE[FilingStatus][data.filing]
    story: list[Flowable] = [
        Paragraph(_t(f"Steuerübersicht {data.year} – belegbot"), st["h1"]),
        Paragraph(
            _t(f"{filing} · erstellt am {de_date(data.today)} · Parameter {s['params_version']}"),
            st["small"],
        ),
        Spacer(1, 4 * mm),
        _table(
            [[Paragraph(_t(DISCLAIMER), st["box"])]],
            [WIDTH],
            [("BOX", (0, 0), (-1, -1), 0.8, colors.black), ("BACKGROUND", (0, 0), (-1, -1), LIGHT)],
        ),
        Paragraph("Schätzung", st["h2"]),
        _table(
            _estimate_rows(s, st),
            [WIDTH - 40 * mm, 40 * mm],
            [("LINEBELOW", (0, 0), (-1, -2), 0.25, colors.lightgrey)],
        ),
    ]
    for flag in _flags(data):
        story.append(Paragraph(_t(flag), st["bold"]))
    story.append(Paragraph("Abgabe", st["h2"]))
    story += [Paragraph(_t(line), st["p"]) for line in _pflicht(data)]
    return story


def _hints(data: ExportData, st: dict[str, ParagraphStyle]) -> list[Flowable]:
    s = data.summary
    story: list[Flowable] = [Paragraph("Nicht berücksichtigt", st["h2"])]
    todo: list[str] = []
    for m in s["what_missing"]:
        code = EstimateMissing(m["code"])
        text = (
            KAP_V_TEXT
            if code is EstimateMissing.KAP_V_NOT_SUPPORTED
            else LABELS_DE[EstimateMissing][code]
        )
        if m["person_id"]:
            text += f" ({data.roles.get(m['person_id'], 'weitere Person')})"
        todo.append(text)
    story += [Paragraph(_t("• " + t), st["p"]) for t in todo] or [Paragraph("–", st["p"])]
    story.append(Paragraph("Hinweise", st["h2"]))
    notes = sorted(
        _note_text(n["code"], n["item_id"], n["person_id"], data) for n in s["notes"]
    )  # by text: stable whatever the person ids are
    if s["attention_count"]:
        notes.append(f"{s['attention_count']} Belege noch zu prüfen")
    for check in s["pflicht"]["not_checked"]:
        notes.append("Nicht geprüft: " + LABELS_DE[PflichtCheck][PflichtCheck(check)])
    story += [Paragraph(_t("• " + t), st["p"]) for t in notes] or [Paragraph("–", st["p"])]
    return story


def _vendor(line: Line) -> str:
    vendor = line.vendor or "–"
    return vendor if len(vendor) <= VENDOR_MAX else vendor[: VENDOR_MAX - 1] + "…"


def _group_rows(
    group: Group, st: dict[str, ParagraphStyle], at: int
) -> tuple[list[list[Any]], list[Any]]:
    label = LABELS_DE[Category][group.category]
    title = Paragraph(
        f"<b>{_t(label)}</b><br/><font size=7 color='#555555'>{_t(group.source)}"
        + ("" if group.zeile else " – Zeile nicht zugeordnet")
        + "</font>",
        st["p"],
    )
    rows: list[list[Any]] = [
        [
            Paragraph(_t(group.zeile or "–"), st["bold"]),
            title,
            "",
            Paragraph(_eur(group.amount), st["boldr"]),
            Paragraph(
                f"{len(group.lines)} {'Beleg' if len(group.lines) == 1 else 'Belege'}", st["right"]
            ),
        ]
    ]
    style: list[Any] = [
        ("SPAN", (1, at), (2, at)),
        ("LINEABOVE", (0, at), (-1, at), 0.5, colors.grey),
    ]
    for line in sorted(group.lines, key=lambda r: (r.day or date.max, r.item_id)):
        when = de_date(line.day) if line.day else "ohne Datum"
        pay = LABELS_DE[PaymentMethod][line.payment]
        extra = []
        if line.labour_35a is not None:
            extra.append(f"Lohnanteil {_eur(line.labour_35a)}")
        if line.unreviewed:
            extra.append("ungeprüft")
        if line.document_id is None:
            extra.append("ohne Beleg")
        rows.append(
            [
                "",
                Paragraph(_t(f"{_vendor(line)}"), st["small"]),
                Paragraph(
                    _t(f"Beleg-Nr. {line.beleg_nr} · {when} · {pay}"),
                    st["small"],
                ),
                Paragraph(
                    _eur(line.deductible),
                    ParagraphStyle("sr", parent=st["small"], alignment=TA_RIGHT),
                ),
                Paragraph(_t(", ".join(extra)), st["small"]),
            ]
        )
    return rows, style


def _anlage_sections(data: ExportData, st: dict[str, ParagraphStyle]) -> list[Flowable]:
    story: list[Flowable] = []
    totals = {a["anlage"]: a["amount"] for a in data.summary["by_anlage"]}
    widths = [12 * mm, 52 * mm, 58 * mm, 26 * mm, WIDTH - 148 * mm]
    for part_title, computed in (("Anlagen und Zeilen", True), ("Nicht in der Schätzung", False)):
        anlagen = [
            a
            for a in Anlage
            if any((g.anlage == a.value and (not g.not_computed) == computed) for g in data.groups)
        ]
        if not anlagen:
            if computed:
                story += [
                    Paragraph(part_title, st["h2"]),
                    Paragraph(_t(f"Keine Belege für {data.year}"), st["p"]),
                ]
            continue
        story.append(Paragraph(part_title, st["h2"]))
        if not computed:
            story.append(
                Paragraph(
                    _t("Diese Belege sind in keiner Summe der Schätzung enthalten."), st["small"]
                )
            )
        for anlage in anlagen:
            story.append(Paragraph(_t(LABELS_DE[Anlage][anlage]), st["anlage"]))
            rows: list[list[Any]] = []
            style: list[Any] = []
            groups = sorted(
                (
                    g
                    for g in data.groups
                    if g.anlage == anlage.value and (not g.not_computed) == computed
                ),
                key=lambda g: (zeile_key(g.zeile), g.category.value),
            )
            for g in groups:
                g_rows, g_style = _group_rows(g, st, len(rows))
                rows += g_rows
                style += g_style
            rows.append(
                [
                    "",
                    Paragraph(_t(f"Summe {LABELS_DE[Anlage][anlage]}"), st["bold"]),
                    "",
                    Paragraph(_eur(totals[anlage.value]), st["boldr"]),
                    "",
                ]
            )
            last = len(rows) - 1
            style += [
                ("SPAN", (1, last), (2, last)),
                ("LINEABOVE", (0, last), (-1, last), 0.8, colors.black),
            ]
            story.append(_table(rows, widths, style))
            story.append(Spacer(1, 4 * mm))
    return story


def pdf_bytes(data: ExportData) -> bytes:
    st = _styles()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=18 * mm,
        bottomMargin=20 * mm,
        title=_plain(f"Steuerübersicht {data.year}"),
        author="belegbot",
        invariant=1,
    )
    story = _head(data, st) + _hints(data, st) + _anlage_sections(data, st)

    def canvas(*args: Any, **kwargs: Any) -> Canvas:
        return _Numbered(data.year, *args, **kwargs)

    doc.build(story, canvasmaker=canvas)
    return buf.getvalue()
