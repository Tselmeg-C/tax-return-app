"""Web side of linking (#11): code, link state, unlink. Session + CSRF like every route."""

from __future__ import annotations

import io
import logging
import re
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import select

from app.auth.service import create_session
from app.bot import messages as m
from app.bot.links import CODE_TTL
from app.db.models import AppUser, ChannelLinkCode
from tests.bot.conftest import Bot, new_chat_id


async def cookie_of(bot: Bot, user: AppUser) -> str:
    token, _ = await create_session(
        bot.session, user, now=bot.clock.now(), max_age=timedelta(days=30)
    )
    await bot.session.commit()
    return token


async def test_needs_session_and_csrf(bot: Bot) -> None:
    chat = await bot.linked()
    cookie = await cookie_of(bot, chat.user)
    for method, path in (("POST", "/bot/link-code"), ("GET", "/bot/link"), ("DELETE", "/bot/link")):
        assert (await bot.api.request(method, path)).status_code == 401
    assert (await bot.api.post("/bot/link-code", cookie=cookie, csrf=False)).status_code == 403
    assert (
        await bot.api.request("DELETE", "/bot/link", cookie=cookie, csrf=False)
    ).status_code == 403


async def test_code_shape_ttl_and_no_store(bot: Bot) -> None:
    chat = await bot.linked()
    cookie = await cookie_of(bot, chat.user)
    response = await bot.api.post("/bot/link-code", cookie=cookie)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert re.fullmatch(r"\d{8}", body["code"])
    assert body["expires_at"] == (bot.clock.now() + CODE_TTL).isoformat()


async def test_code_links_once_and_a_new_code_replaces_the_old(bot: Bot) -> None:
    chat = await bot.linked()
    await bot.api.request("DELETE", "/bot/link", cookie=await cookie_of(bot, chat.user))
    cookie = await cookie_of(bot, chat.user)
    first = (await bot.api.post("/bot/link-code", cookie=cookie)).json()["code"]
    second = (await bot.api.post("/bot/link-code", cookie=cookie)).json()["code"]
    assert first != second
    rows = (await bot.session.execute(select(ChannelLinkCode))).scalars().all()
    assert sorted(r.used_at is None for r in rows) == [False, True]
    new_chat = new_chat_id()
    assert await bot.send(new_chat, f"/link {first}") == [m.LINK_FAILED]
    assert await bot.send(new_chat, f"/link {second}") == [m.LINKED]
    assert (await bot.api.get("/bot/link", cookie=cookie)).json() == {
        "linked": True,
        "channel": "telegram",
    }


async def test_link_state_is_per_user_and_unlink_is_idempotent(bot: Bot) -> None:
    chat = await bot.linked()
    colleague = await bot.linked(chat.household)  # same household, own chat
    cookie = await cookie_of(bot, chat.user)
    assert (await bot.api.get("/bot/link", cookie=cookie)).json()["linked"] is True
    assert "external_id" not in (await bot.api.get("/bot/link", cookie=cookie)).text
    assert (await bot.api.request("DELETE", "/bot/link", cookie=cookie)).status_code == 204
    assert (await bot.api.request("DELETE", "/bot/link", cookie=cookie)).status_code == 204
    assert (await bot.api.get("/bot/link", cookie=cookie)).json()["linked"] is False
    assert await bot.send(chat.chat_id, "/year") == []  # silent again
    assert await bot.send(colleague.chat_id, "/year") != []  # the other member is untouched


async def test_code_never_in_logs(
    bot: Bot, json_log: io.StringIO, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    chat = await bot.linked()
    cookie = await cookie_of(bot, chat.user)
    code = (await bot.api.post("/bot/link-code", cookie=cookie)).json()["code"]
    await bot.send(new_chat_id(), f"/link {code}")
    assert code not in json_log.getvalue() + caplog.text


async def test_code_belongs_to_the_users_household(bot: Bot) -> None:
    chat = await bot.linked()
    cookie = await cookie_of(bot, chat.user)
    await bot.api.post("/bot/link-code", cookie=cookie)
    row = (await bot.session.execute(select(ChannelLinkCode))).scalar_one()
    assert row.household_id == chat.household.id and row.user_id == chat.user.id
    _: Any = None
