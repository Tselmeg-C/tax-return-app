"""All bot texts (German). Replies to a linked chat may hold amounts and vendors; logs never."""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal

from app.domain.enums import LABELS_DE, AttentionReason

HELP = (
    "Ich sammle deine Belege für die Steuererklärung.\n"
    "Schick mir ein Foto oder PDF eines Belegs.\n\n"
    "/summary [Jahr] - Schätzung\n"
    "/missing [Jahr] - was noch fehlt\n"
    "/year <Jahr> - Standardjahr\n"
    "/undo - letzten Beleg löschen\n"
    "/unlink - Verknüpfung lösen\n"
    "Verknüpfen: /link <Code> (Code im Web unter Haushalt)"
)
LINK_FAILED = "Code ungültig oder abgelaufen"
LINKED = "Verknüpft"
ALREADY_LINKED = "Dieser Chat ist schon verknüpft"
UNLINKED = "Verknüpfung gelöst"
FILE_UNSUPPORTED = "Dateityp nicht unterstützt"
FILE_TOO_BIG = "Datei zu groß"
FILE_EMPTY = "Die Datei ist leer"
FILE_FETCH_FAILED = "Die Datei konnte nicht geladen werden, bitte noch einmal senden"
STORAGE_FULL = "Speicher voll, bitte später noch einmal versuchen"
RECEIVED = "Eingegangen, ich melde mich gleich"
DUPLICATE = "Beleg schon vorhanden"
DUPLICATE_REQUEUED = "Beleg schon vorhanden, wird erneut verarbeitet"
UNDONE = "Letzten Beleg gelöscht"
UNDO_NOTHING = "Kein Beleg zum Löschen"
UNDO_BUSY = "Der Beleg wird gerade verarbeitet, bitte kurz warten"
UNDO_OVERRIDDEN = "Dieser Beleg wurde von dir bearbeitet, bitte in der Web-App löschen"
NO_PROFILE = "Für dieses Jahr ist noch kein Haushalt eingerichtet (Web-App)"
YEAR_BAD = "Bitte ein Jahr angeben, z. B. /year 2025"
NOTHING_MISSING = "Es fehlt nichts, soweit ich das sehe"
FAILED_DOC = "Der Beleg konnte nicht verarbeitet werden. Bitte in der Web-App prüfen."
DONE_NO_ITEM = "Beleg verarbeitet, aber keine absetzbare Position erkannt"


def eur(value: Decimal | str) -> str:
    """`1234.5` -> `1.234,50 EUR` (German format)."""
    amount = Decimal(value).quantize(Decimal("0.01"))
    text = f"{abs(amount):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{'-' if amount < 0 else ''}{text} EUR"


def unsupported_year(years: Iterable[int]) -> str:
    return "Jahr nicht unterstützt. Möglich: " + ", ".join(str(y) for y in years)


def year_set(year: int) -> str:
    return f"Standardjahr: {year}"


def needs_attention(reason: AttentionReason | None, base_url: str) -> str:
    label = LABELS_DE[AttentionReason][reason] if reason else "Prüfung nötig"
    return f"Beleg braucht deine Prüfung: {label}\n{base_url}/belege"
