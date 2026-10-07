"""Helpers for the handler tests: scripted routers, a content-capturing provider, a world."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import LLMSettings, Settings
from app.db.models import AppUser, Document, Extraction, Household, Job, Person, TaxItem
from app.domain.enums import ExtractionStep, PersonKind, UserRole
from app.llm import LLMError, LLMRequest, LLMResult, LLMRouter
from app.llm.fake import FakeProvider, FakeReply
from app.pipeline.handler import PipelineHandler
from app.pipeline.schemas import ClassifyOutput, GenericBillExtraction
from app.storage import LocalVolume
from tests.documents.test_worker import make_worker, upload

Sessions = async_sessionmaker[AsyncSession]


async def _no_sleep(_: float) -> None:
    return None


def llm_settings(**kw: Any) -> LLMSettings:
    values: dict[str, Any] = {
        "llm_classify_model": "fake:test",
        "llm_extract_model": "fake:test",
        "llm_fallback_model": "none",
        "llm_max_attempts": 1,
    }
    values.update(kw)
    return LLMSettings(_env_file=None, **values)  # type: ignore[call-arg]


class CapturingProvider(FakeProvider):
    """A FakeProvider that keeps what was sent (system prompt + parts) for privacy checks."""

    def __init__(self, script: Sequence[FakeReply | LLMError]) -> None:
        super().__init__(script, app_env="development")
        self.systems: list[str] = []
        self.parts: list[Any] = []

    async def structured(self, request: LLMRequest[Any]) -> LLMResult[Any]:
        self.systems.append(request.system)
        self.parts.extend(request.parts)
        return await super().structured(request)


def replies(*items: BaseModel | LLMError) -> list[FakeReply | LLMError]:
    return [i if isinstance(i, LLMError) else FakeReply(data=i) for i in items]


@dataclass
class Scripted:
    provider: CapturingProvider
    router: LLMRouter

    @property
    def calls(self) -> int:
        return self.provider.requests_seen


def scripted(*items: BaseModel | LLMError, **llm: Any) -> Scripted:
    provider = CapturingProvider(replies(*items))
    router = LLMRouter(llm_settings(**llm), providers={"fake": provider}, sleep=_no_sleep)
    return Scripted(provider, router)


def classify_out(**kw: Any) -> ClassifyOutput:
    values: dict[str, Any] = {
        "doc_type": "generic_bill",
        "tax_relevant": True,
        "readable": True,
        "multiple_documents": False,
        "total_gross": "312.40",
        "currency": "EUR",
        "document_date": "2025-11-03",
        "vendor": "Malerbetrieb Beispiel GmbH",
        "certificate_year": None,
        "reason_de": "Handwerkerrechnung.",
        "confidence": "high",
    }
    values.update(kw)
    return ClassifyOutput.model_validate(values)


def extract_out(
    lines: list[tuple[str, str, str]] | None = None, **kw: Any
) -> GenericBillExtraction:
    lines = lines or [("180.00", "wk_arbeitsmittel", "not_applicable")]
    total = sum(float(a) for a, _, _ in lines)
    values: dict[str, Any] = {
        "vendor": "Malerbetrieb Beispiel GmbH",
        "recipient_name": "Herr Alex Muster",
        "invoice_date": "2025-11-03",
        "payment_date": "2025-11-05",
        "payment_method": "bank_transfer",
        "currency": "EUR",
        "total_gross": f"{total:.2f}",
        "total_vat": None,
        "is_credit_note": False,
        "line_items": [
            {"description": "Position", "gross_amount": a, "category": c, "cost_kind_35a": k}
            for a, c, k in lines
        ],
        "stated_labour_amount_35a": None,
        "reason_de": "Rechnung Arbeitsmittel.",
        "confidence": "high",
    }
    values.update(kw)
    return GenericBillExtraction.model_validate(values)


@dataclass
class World:
    sessions: Sessions
    volume: LocalVolume
    database_url: str
    household_id: uuid.UUID
    user_id: uuid.UUID
    persons: dict[str, uuid.UUID] = field(default_factory=dict)

    def settings(self, **kw: Any) -> Settings:
        values: dict[str, Any] = {"database_url": self.database_url}
        values.update(kw)
        return Settings(_env_file=None, **values)  # type: ignore[call-arg]

    def handler(self, router: LLMRouter) -> PipelineHandler:
        return PipelineHandler(router=lambda: router)

    def worker(self, router: LLMRouter, **overrides: Any) -> Any:
        return make_worker(
            self.sessions, self.volume, self.database_url, self.handler(router), **overrides
        )

    async def upload(self, data: bytes, **doc_values: Any) -> Document:
        from tests.documents.conftest import CommittedUser

        doc = await upload(
            self.sessions, self.volume, CommittedUser(self.household_id, self.user_id, ""), data
        )
        if doc_values:
            from sqlalchemy import update

            async with self.sessions() as session:
                await session.execute(
                    update(Document).where(Document.id == doc.id).values(**doc_values)
                )
                await session.commit()
        return doc

    async def add_person(self, first: str, last: str | None = "Muster") -> uuid.UUID:
        async with self.sessions() as session:
            person = Person(
                household_id=self.household_id,
                kind=PersonKind.ADULT,
                first_name=first,
                last_name=last,
            )
            session.add(person)
            await session.commit()
            self.persons[first] = person.id
            return person.id

    async def set_uploader_person(self, person_id: uuid.UUID | None) -> None:
        from sqlalchemy import update

        async with self.sessions() as session:
            await session.execute(
                update(AppUser).where(AppUser.id == self.user_id).values(person_id=person_id)
            )
            await session.commit()

    async def rows(self, model: Any, document_id: uuid.UUID) -> list[Any]:
        async with self.sessions() as session:
            stmt = select(model).where(model.document_id == document_id)
            if hasattr(model, "created_at"):
                stmt = stmt.order_by(model.created_at)
            return list((await session.execute(stmt)).scalars().all())

    async def doc(self, document_id: uuid.UUID) -> Document:
        async with self.sessions() as session:
            doc = await session.get(Document, document_id)
            assert doc is not None
            return doc

    async def job(self, document_id: uuid.UUID) -> Job:
        rows = await self.rows(Job, document_id)
        return rows[-1]

    async def extraction_count(self, document_id: uuid.UUID, step: ExtractionStep) -> int:
        async with self.sessions() as session:
            result = await session.execute(
                select(func.count())
                .select_from(Extraction)
                .where(Extraction.document_id == document_id, Extraction.step == step)
            )
            return int(result.scalar_one())

    async def items(self, document_id: uuid.UUID) -> list[TaxItem]:
        return await self.rows(TaxItem, document_id)


async def make_world(
    sessions: Sessions, tmp_path: Path, database_url: str, email: str | None = None
) -> World:
    volume = LocalVolume(tmp_path / "storage")
    volume.probe()
    async with sessions() as session:
        hh = Household(name="Testhaushalt")
        session.add(hh)
        await session.flush()
        user = AppUser(
            household_id=hh.id,
            email=email or f"user-{uuid.uuid4().hex[:8]}@example.com",
            role=UserRole.OWNER,
        )
        session.add(user)
        await session.commit()
        return World(sessions, volume, database_url, hh.id, user.id)
