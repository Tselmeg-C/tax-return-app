"""Small reportlab helper shared by the templates: cursor layout, tables, footer on every page.

Determinism: `invariant=1`, standard PDF fonts only (Helvetica), fixed metadata.
"""

from __future__ import annotations

import io
from collections.abc import Sequence
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen.canvas import Canvas

FOOTER = "Synthetisches Testdokument – belegbot evals"
PDF_AUTHOR = "belegbot evals"
PDF_CREATOR = "belegbot evals synth"

HOUSEHOLD_ADDRESS = ("Musterstraße 1", "12345 Musterstadt")

MONTHS_DE = (
    "Januar",
    "Februar",
    "März",
    "April",
    "Mai",
    "Juni",
    "Juli",
    "August",
    "September",
    "Oktober",
    "November",
    "Dezember",
)
MONTHS_EN = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)

CENT = Decimal("0.01")


def eur_de(amount: Decimal, symbol: bool = True) -> str:
    q = amount.quantize(CENT, rounding=ROUND_HALF_UP)
    sign = "-" if q < 0 else ""
    whole, frac = f"{abs(q):.2f}".split(".")
    groups: list[str] = []
    while len(whole) > 3:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    groups.insert(0, whole)
    text = f"{sign}{'.'.join(groups)},{frac}"
    return f"{text} €" if symbol else text


def eur_en(amount: Decimal) -> str:
    q = amount.quantize(CENT, rounding=ROUND_HALF_UP)
    sign = "-" if q < 0 else ""
    return f"{sign}€{abs(q):,.2f}"


def date_de(d: date) -> str:
    return d.strftime("%d.%m.%Y")


def date_en(d: date) -> str:
    return f"{d.day} {MONTHS_EN[d.month - 1]} {d.year}"


class Writer:
    """A top-down text cursor over a reportlab canvas. Draws the footer on every page."""

    def __init__(
        self,
        pagesize: tuple[float, float] = A4,
        margin: float = 20 * mm,
        footer_size: float = 7.5,
        page_numbers: bool = False,
    ) -> None:
        self.buf = io.BytesIO()
        self.pagesize = pagesize
        self.width, self.height = pagesize
        self.margin = margin
        self.footer_size = footer_size
        self.page_numbers = page_numbers
        self.page = 1
        self.c = Canvas(self.buf, pagesize=pagesize, invariant=1, pageCompression=1)
        self.c.setAuthor(PDF_AUTHOR)
        self.c.setCreator(PDF_CREATOR)
        self.c.setTitle("Synthetisches Testdokument")
        self.c.setSubject("")
        self.c.setKeywords("")
        self.y = self.height - margin

    # --- page handling ---------------------------------------------------------------

    @property
    def left(self) -> float:
        return self.margin

    @property
    def right(self) -> float:
        return self.width - self.margin

    @property
    def bottom(self) -> float:
        return self.margin + 6 * mm

    def _footer(self) -> None:
        self.c.setFont("Helvetica", self.footer_size)
        self.c.setFillGray(0.35)
        y = self.margin / 2
        self.c.drawCentredString(self.width / 2, y, FOOTER)
        if self.page_numbers:
            self.c.drawRightString(self.right, y, f"Seite {self.page}")
        self.c.setFillGray(0)

    def new_page(self) -> None:
        self._footer()
        self.c.showPage()
        self.page += 1
        self.y = self.height - self.margin

    def ensure(self, height: float) -> None:
        if self.y - height < self.bottom:
            self.new_page()

    def finish(self) -> bytes:
        self._footer()
        self.c.showPage()
        self.c.save()
        return self.buf.getvalue()

    # --- text ------------------------------------------------------------------------

    def font(self, size: float, bold: bool = False) -> None:
        self.c.setFont("Helvetica-Bold" if bold else "Helvetica", size)

    def line(
        self,
        text: str,
        size: float = 10,
        bold: bool = False,
        align: str = "left",
        x: float | None = None,
        gap: float = 1.35,
    ) -> None:
        self.ensure(size * gap)
        self.y -= size * gap
        self.font(size, bold)
        if align == "center":
            self.c.drawCentredString(self.width / 2 if x is None else x, self.y, text)
        elif align == "right":
            self.c.drawRightString(self.right if x is None else x, self.y, text)
        else:
            self.c.drawString(self.left if x is None else x, self.y, text)

    def pair(
        self, left: str, right: str, size: float = 10, bold: bool = False, x: float | None = None
    ) -> None:
        """Label on the left, value right-aligned (totals, receipt lines)."""
        self.ensure(size * 1.35)
        self.y -= size * 1.35
        self.font(size, bold)
        self.c.drawString(self.left if x is None else x, self.y, left)
        self.c.drawRightString(self.right, self.y, right)

    def gap(self, points: float) -> None:
        self.y -= points

    def rule(self, weight: float = 0.5) -> None:
        self.ensure(4)
        self.y -= 3
        self.c.setLineWidth(weight)
        self.c.line(self.left, self.y, self.right, self.y)
        self.y -= 2

    def paragraph(self, text: str, size: float = 10, width_chars: int = 95) -> None:
        for chunk in wrap(text, width_chars):
            self.line(chunk, size=size)

    def table(
        self,
        headers: Sequence[str],
        rows: Sequence[Sequence[str]],
        widths: Sequence[float],
        align: Sequence[str],
        size: float = 9.5,
        on_page_break: str | None = None,
    ) -> None:
        """Simple grid without borders; `widths` are fractions of the usable width."""
        usable = self.right - self.left
        xs = [self.left]
        for w in widths[:-1]:
            xs.append(xs[-1] + w * usable)

        def draw_row(cells: Sequence[str], bold: bool) -> None:
            self.y -= size * 1.45
            self.font(size, bold)
            for i, cell in enumerate(cells):
                if align[i] == "right":
                    x_right = xs[i] + widths[i] * usable - 2
                    self.c.drawRightString(x_right, self.y, cell)
                else:
                    self.c.drawString(xs[i] + 1, self.y, cell)

        self.ensure(size * 3)
        draw_row(headers, True)
        self.rule(0.4)
        for row in rows:
            if self.y - size * 1.45 < self.bottom:
                if on_page_break:
                    self.line(on_page_break, size=size, align="right")
                self.new_page()
                draw_row(headers, True)
                self.rule(0.4)
            draw_row(row, False)
        self.rule(0.4)

    # --- common blocks ---------------------------------------------------------------

    def letterhead(self, name: str, address: Sequence[str], extra: Sequence[str] = ()) -> None:
        self.line(name, size=15, bold=True)
        self.line("  ·  ".join([*address, *extra]), size=8)
        self.gap(10)

    def address_block(self, lines: Sequence[str], meta: Sequence[tuple[str, str]] = ()) -> None:
        top = self.y
        for text in lines:
            self.line(text, size=10)
        bottom = self.y
        self.y = top
        for key, value in meta:
            self.y -= 10 * 1.35
            self.font(9)
            self.c.drawString(self.left + 0.58 * (self.right - self.left), self.y, key)
            self.c.drawRightString(self.right, self.y, value)
        self.y = min(bottom, self.y) - 14


def wrap(text: str, width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        if current and len(current) + 1 + len(word) > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}" if current else word
    if current:
        lines.append(current)
    return lines


_ONES = (
    "",
    "eins",
    "zwei",
    "drei",
    "vier",
    "fünf",
    "sechs",
    "sieben",
    "acht",
    "neun",
    "zehn",
    "elf",
    "zwölf",
    "dreizehn",
    "vierzehn",
    "fünfzehn",
    "sechzehn",
    "siebzehn",
    "achtzehn",
    "neunzehn",
)
_TENS = (
    "",
    "",
    "zwanzig",
    "dreißig",
    "vierzig",
    "fünfzig",
    "sechzig",
    "siebzig",
    "achtzig",
    "neunzig",
)


def words_de(n: int) -> str:
    """German number words for 0 <= n < 1 000 000 (Zuwendungsbestätigung)."""
    if n == 0:
        return "null"
    if n >= 1000:
        thousands, rest = divmod(n, 1000)
        head = "ein" if thousands == 1 else words_de(thousands)
        return f"{head}tausend{words_de(rest) if rest else ''}"
    if n >= 100:
        hundreds, rest = divmod(n, 100)
        head = "ein" if hundreds == 1 else _ONES[hundreds]
        return f"{head}hundert{words_de(rest) if rest else ''}"
    if n < 20:
        return _ONES[n]
    tens, ones = divmod(n, 10)
    if ones == 0:
        return _TENS[tens]
    one = "ein" if ones == 1 else _ONES[ones]
    return f"{one}und{_TENS[tens]}"
