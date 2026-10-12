"""Deductions without Vorsorge, agB and § 35a (#15): pure functions on frozen DTOs."""

from app.tax.deductions.agb import agb, behinderten_pauschbetrag, zumutbare_belastung
from app.tax.deductions.models import (
    UNASSIGNED,
    AgbResult,
    ChildInput,
    ChildLine,
    EmploymentInput,
    ItemInput,
    Note,
    PersonInput,
    PersonWk,
    ReturnContext,
    SonderausgabenResult,
    VorsorgeInput,
    VorsorgeResult,
    WerbungskostenResult,
)
from app.tax.deductions.sonderausgaben import sonderausgaben
from app.tax.deductions.vorsorge import vorsorge, vorsorge_from_items
from app.tax.deductions.werbungskosten import (
    entfernungspauschale,
    homeoffice_pauschale,
    werbungskosten,
)

__all__ = [
    "AgbResult",
    "PersonInput",
    "agb",
    "behinderten_pauschbetrag",
    "zumutbare_belastung",
    "UNASSIGNED",
    "ChildInput",
    "ChildLine",
    "EmploymentInput",
    "ItemInput",
    "Note",
    "PersonWk",
    "ReturnContext",
    "SonderausgabenResult",
    "VorsorgeInput",
    "VorsorgeResult",
    "WerbungskostenResult",
    "entfernungspauschale",
    "homeoffice_pauschale",
    "sonderausgaben",
    "vorsorge",
    "vorsorge_from_items",
    "werbungskosten",
]
