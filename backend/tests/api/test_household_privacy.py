"""#13 privacy: a generated Steuer-ID and sentinel names / employer go through create, patch,
a 422, a 409, copy and delete; none of them is in a log line, span attribute, metric attribute,
error body or `audit_log` row. Plus: the Steuer-ID is unreachable from pipeline / LLM / evals.
"""

from __future__ import annotations

import io
import json
import re
import secrets
from pathlib import Path
from typing import Any

from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy import select

from app.db.models import AuditLog
from tests.api.conftest import T
from tests.api.test_household import call, profile
from tests.documents.conftest import metric_points
from tests.household.steuer_ids import generate_steuer_id, spaced

BACKEND = Path(__file__).resolve().parents[2]


async def test_no_sentinel_leaks(
    t: T,
    json_log: io.StringIO,
    spans: InMemorySpanExporter,
    metric_reader: InMemoryMetricReader,
) -> None:
    tag = secrets.token_hex(6)
    first, last, employer = f"First{tag}", f"Last{tag}", f"Employer{tag}"
    sid, other_sid = generate_steuer_id(), generate_steuer_id()
    a = await t.home()
    errors: list[str] = []

    r = await call(
        t, a, "POST", "/persons", {"kind": "adult", "first_name": first, "steuer_id": sid}
    )
    assert r.status_code == 201
    me = r.json()["id"]
    bodies = [r.text]
    r = await call(t, a, "PATCH", f"/persons/{me}", {"last_name": last, "steuer_id": other_sid})
    assert r.status_code == 200
    bodies.append(r.text)
    bad = await call(
        t, a, "POST", "/persons", {"kind": "adult", "first_name": first, "steuer_id": spaced(sid)}
    )
    assert bad.status_code == 201  # sid was replaced, so it is free again
    dup = await call(
        t, a, "POST", "/persons", {"kind": "adult", "first_name": last, "steuer_id": sid}
    )
    assert dup.status_code == 422
    errors.append(dup.text)
    wrong = sid[:10] + str((int(sid[10]) + 1) % 10)
    invalid = await call(t, a, "PATCH", f"/persons/{me}", {"steuer_id": wrong})
    assert invalid.status_code == 422
    errors.append(invalid.text)

    await profile(t, a, 2025, me)
    job = await call(
        t,
        a,
        "POST",
        "/employments",
        {"person_id": me, "year": 2025, "employer_name": employer, "steuerklasse": "1"},
    )
    assert job.status_code == 201
    in_profile = await call(t, a, "DELETE", f"/persons/{me}")
    assert in_profile.status_code == 409
    errors.append(in_profile.text)
    copied = await call(t, a, "POST", "/household/2026/copy", {"from_year": 2025})
    assert copied.status_code == 200
    gone = await call(t, a, "DELETE", f"/persons/{bad.json()['id']}")
    assert gone.status_code == 204
    bodies.append((await call(t, a, "GET", "/household/2025")).text)
    bodies.append((await call(t, a, "GET", "/persons")).text)

    logs = json_log.getvalue()
    assert '"household.person_created"' in logs and '"household.rejected"' in logs
    assert '"household.copied"' in logs and '"household.person_deleted"' in logs
    span_values = [
        str(v) for s in spans.get_finished_spans() for v in (s.attributes or {}).values()
    ]
    assert span_values
    metric_values: list[Any] = [
        str(v)
        for points in metric_points(metric_reader).values()
        for p in points
        for v in (p.attributes or {}).values()
    ]
    audit = json.dumps(
        [[r.before, r.after] for r in (await t.session.execute(select(AuditLog))).scalars()]
    )
    for value in (sid, other_sid, spaced(sid), wrong):
        assert value not in audit and value not in logs
        for body in bodies:
            assert value not in body
    for sentinel in (sid, other_sid, wrong, first, last, employer, tag):
        assert sentinel not in logs, sentinel
        assert not [v for v in span_values if sentinel in v], sentinel
        assert not [v for v in metric_values if sentinel in v], sentinel
        for body in errors:
            assert sentinel not in body, sentinel


def test_steuer_id_unreachable_from_pipeline_llm_and_evals() -> None:
    """The Steuer-ID can never reach a prompt: no module there names the attribute."""
    pattern = re.compile(r"\bsteuer_id\b")
    roots = [BACKEND / "app" / "pipeline", BACKEND / "app" / "llm", BACKEND / "evals"]
    offenders = [
        str(path.relative_to(BACKEND))
        for root in roots
        for path in root.rglob("*")
        if path.is_file()
        and path.suffix in {".py", ".md", ".yaml", ".yml", ".json", ".txt"}
        and pattern.search(path.read_text(encoding="utf-8", errors="ignore"))
    ]
    assert offenders == []
