"""Category → (Anlage, Zeile) per year (#9). Pure: the table comes from `app.tax_params`."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from app.domain.enums import Anlage, Category
from app.tax.models import MappingEntry

MappingTable = Mapping[int, Mapping[Category, MappingEntry]]
"""`{year: {category: entry}}`, one entry per supported year (`params/{year}.yaml`)."""


@dataclass(frozen=True)
class FormLine:
    anlage: Anlage | None
    zeile: str | None
    supported: bool  # False: no params file for the year


def map_category(category: Category, year: int, table: MappingTable) -> FormLine:
    """`irrelevant` → both NULL; a year without params → both NULL and `supported=False`."""
    if year not in table:
        return FormLine(None, None, supported=False)
    if category is Category.IRRELEVANT:
        return FormLine(None, None, supported=True)
    entry = table[year][category]
    return FormLine(entry.anlage, entry.zeile, supported=True)
