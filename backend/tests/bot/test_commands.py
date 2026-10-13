"""`/summary` and `/missing` (#11): same numbers as `GET /summary`, rendered in German."""

from __future__ import annotations

import copy
from datetime import timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from app.auth.service import create_session
from app.bot import messages as m
from app.bot.links import create_link
from app.db.models import AppUser, Household
from app.db.scope import HouseholdScope
from app.domain.enums import Channel
from tests.bot.conftest import Bot, new_chat_id
from tests.estimate.helpers import build, load_yaml

SCENARIOS = {s["id"]: s for s in load_yaml("golden_2025.yaml")["scenarios"]}
SINGLE = SCENARIOS["s1_single_wage_commute"]
JOINT = SCENARIOS["s2_joint_iii_v_church"]


async def chat_for(bot: Bot, household: Household) -> str:
    user = (
        await bot.session.execute(select(AppUser).where(AppUser.household_id == household.id))
    ).scalar_one()
    chat = new_chat_id()
    await create_link(HouseholdScope(bot.session, household.id), user.id, Channel.TELEGRAM, chat)
    return chat


async def web_summary(bot: Bot, household: Household, year: int) -> dict[str, Any]:
    user = (
        await bot.session.execute(select(AppUser).where(AppUser.household_id == household.id))
    ).scalar_one()
    token, _ = await create_session(
        bot.session, user, now=bot.clock.now(), max_age=timedelta(days=30)
    )
    await bot.session.commit()
    response = await bot.api.get(f"/summary?year={year}", cookie=token)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def eur(value: str) -> str:
    return m.eur(Decimal(value))


async def test_summary_matches_the_web_summary(bot: Bot) -> None:
    built = await build(bot.session, SINGLE)
    chat = await chat_for(bot, built.household)
    web = await web_summary(bot, built.household, 2025)
    [reply] = await bot.send(chat, "/summary 2025")
    assert reply.startswith("Steuerschätzung 2025")
    assert f"Erstattung: {eur(web['refund'])}" in reply
    for row in web["by_anlage"]:
        assert eur(row["amount"]) in reply
    assert "Nachzahlung" not in reply
    assert "Abgabefrist:" in reply


async def test_summary_nachzahlung_never_a_negative_erstattung(bot: Bot) -> None:
    built = await build(bot.session, JOINT)
    chat = await chat_for(bot, built.household)
    web = await web_summary(bot, built.household, 2025)
    assert Decimal(web["refund"]) < 0
    [reply] = await bot.send(chat, "/summary 2025")
    assert f"Nachzahlung: {eur(str(-Decimal(web['refund'])))}" in reply
    assert "Erstattung" not in reply


async def test_summary_year_default_and_unsupported(bot: Bot) -> None:
    built = await build(bot.session, SINGLE)
    chat = await chat_for(bot, built.household)
    await bot.send(chat, "/year 2025")
    [with_default] = await bot.send(chat, "/summary")
    [explicit] = await bot.send(chat, "/summary 2025")
    assert with_default == explicit
    [bad] = await bot.send(chat, "/summary 1999")
    assert bad.startswith("Jahr nicht unterstützt") and "2025" in bad
    assert await bot.send(chat, "/summary abc") == [m.YEAR_BAD]
    # 2026 is supported but this household has no profile for it
    assert await bot.send(chat, "/summary 2026") == [m.NO_PROFILE]


async def test_missing_names_the_person_through_the_scope(bot: Bot) -> None:
    spec = copy.deepcopy(SINGLE)
    spec["employments"] = [{"person": "a", "steuerklasse": "1"}]
    built = await build(bot.session, spec)
    chat = await chat_for(bot, built.household)
    web = await web_summary(bot, built.household, 2025)
    codes = {w["code"] for w in web["what_missing"]}
    assert "no_lstb" in codes
    [reply] = await bot.send(chat, "/missing 2025")
    assert "Lohnsteuerbescheinigung fehlt" in reply and "(Mustera)" in reply
    assert reply.count("\n- ") == len(web["what_missing"])


async def test_missing_nothing_and_no_profile(bot: Bot) -> None:
    built = await build(bot.session, SINGLE)
    chat = await chat_for(bot, built.household)
    assert await bot.send(chat, "/missing 2025") == [m.NOTHING_MISSING]
    assert await bot.send(chat, "/missing 2026") == [m.NO_PROFILE]
