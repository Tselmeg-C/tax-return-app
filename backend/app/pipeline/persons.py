"""Recipient name → household person (#9 Scope "Handler" 5). Names never leave this module
(not to the LLM, logs or metrics)."""

from __future__ import annotations

import re
import unicodedata
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AppUser, Person
from app.db.scope import HouseholdScope
from app.domain.enums import CATEGORY_GROUP, Category, CategoryGroup

_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})
_TITLES = re.compile(r"\b(herr|frau|dr|prof)\b\.?")
_PUNCT = re.compile(r"[^\w\s]")


def normalise(name: str) -> list[str]:
    text = name.casefold().translate(_UMLAUTS)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = _PUNCT.sub(" ", _TITLES.sub(" ", text))
    return text.split()


def _matches(tokens: list[str], person: Person) -> bool:
    first = normalise(person.first_name)
    last = normalise(person.last_name or "")
    if not first or not last or len(tokens) < 2:
        return False
    if tokens[-len(last) :] != last:
        return False
    given = tokens[: -len(last)]
    if given == first:
        return True
    # last name + first initial ("A. Muster")
    return len(given) == 1 and len(given[0]) == 1 and given[0] == first[0][0]


async def match_person(
    session: AsyncSession,
    household_id: uuid.UUID,
    uploader_id: uuid.UUID,
    recipient_name: str | None,
    category: Category,
) -> uuid.UUID | None:
    """Exactly one matching person, else the uploader's person, else NULL. §35a: NULL."""
    if CATEGORY_GROUP[category] is CategoryGroup.HAUSHALTSNAHE:
        return None
    scope = HouseholdScope(session, household_id)
    if recipient_name:
        tokens = normalise(recipient_name)
        persons = (await session.execute(scope.select(Person))).scalars().all()
        found = [p for p in persons if _matches(tokens, p)]
        if len(found) == 1:
            return found[0].id
    uploader = await scope.get(AppUser, uploader_id)
    return uploader.person_id if uploader is not None else None
