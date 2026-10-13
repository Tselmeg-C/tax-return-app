"""Deductions (#15, #16, #76, #77): pure functions on frozen DTOs."""

from app.tax.deductions.agb import agb, behinderten_pauschbetrag, zumutbare_belastung
from app.tax.deductions.entlastungsbetrag import entlastungsbetrag
from app.tax.deductions.haushaltsnahe import haushaltsnahe
from app.tax.deductions.models import (
    UNASSIGNED,
    AgbResult,
    ChildInput,
    ChildLine,
    EmploymentInput,
    EntlastungsbetragResult,
    HaushaltsnaheLine,
    HaushaltsnaheResult,
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
    "HaushaltsnaheLine",
    "HaushaltsnaheResult",
    "haushaltsnahe",
    "EmploymentInput",
    "EntlastungsbetragResult",
    "entlastungsbetrag",
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
