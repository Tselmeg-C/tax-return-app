"""Chat text of `/summary` and `/missing`. Every number comes from `build_summary`: the bot
never computes tax. Positive refund = Erstattung, negative = Nachzahlung (never a negative
"Erstattung")."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from app.bot import messages as m
from app.domain.enums import LABELS_DE, Anlage, EstimateMissing


def render_summary(year: int, result: dict[str, Any]) -> str:
    if result["status"] != "ok":
        return m.NO_PROFILE
    refund = Decimal(result["refund"])
    head = f"Steuerschätzung {year}" + (" (vorläufig)" if result["provisional"] else "")
    lines = [
        head,
        f"Erstattung: {m.eur(refund)}" if refund >= 0 else f"Nachzahlung: {m.eur(-refund)}",
    ]
    for row in result["by_anlage"]:
        lines.append(f"{LABELS_DE[Anlage][Anlage(row['anlage'])]}: {m.eur(row['amount'])}")
    pflicht = result["pflicht"]
    if pflicht is not None:
        deadline = date.fromisoformat(pflicht["deadline"]).strftime("%d.%m.%Y")
        lines.append(f"Abgabefrist: {deadline}" + (" (überfällig)" if pflicht["overdue"] else ""))
    if result["attention_count"]:
        lines.append(f"{result['attention_count']} Beleg(e) brauchen deine Prüfung")
    return "\n".join(lines)


def render_missing(result: dict[str, Any], names: dict[str, str]) -> str:
    if result["status"] != "ok":
        return m.NO_PROFILE
    lines = []
    for entry in result["what_missing"]:
        label = LABELS_DE[EstimateMissing][EstimateMissing(entry["code"])]
        name = names.get(entry["person_id"] or "")
        lines.append(f"- {label}" + (f" ({name})" if name else ""))
    return "Das fehlt noch:\n" + "\n".join(lines) if lines else m.NOTHING_MISSING
