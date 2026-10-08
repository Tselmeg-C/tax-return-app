"""#10 privacy: vendor, reason, person names, file name and amounts are runtime sentinels; after
a PATCH, a 422, two 409s and a delete none of them is in a log line, span attribute, metric
attribute or error body. (The `version_conflict` body carries the current item by design, so
only its logs / spans / metrics are checked.)"""

from __future__ import annotations

import io
import secrets
from decimal import Decimal
from typing import Any

from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.domain.enums import AttentionReason, DocumentStatus
from tests.api.conftest import T
from tests.documents.conftest import metric_points
from tests.domain.factories import make_person


def _amount() -> str:
    return f"{secrets.randbelow(900_000) + 100_000}.{secrets.randbelow(90) + 10}"


async def test_no_sentinel_leaks(
    t: T,
    json_log: io.StringIO,
    spans: InMemorySpanExporter,
    metric_reader: InMemoryMetricReader,
) -> None:
    tag = secrets.token_hex(6)
    vendor, reason = f"Vendor{tag}", f"Reason{tag}"
    first, last, filename = f"First{tag}", f"Last{tag}", f"file{tag}.pdf"
    gross, deductible, bad = _amount(), _amount(), _amount()
    a = await t.home()
    person = await make_person(t.session, a.household, first_name=first, last_name=last)
    await t.session.commit()
    doc = await t.doc(
        a,
        original_filename=filename,
        status=DocumentStatus.NEEDS_ATTENTION,
        attention_reason=AttentionReason.LOW_CONFIDENCE,
    )
    item = await t.item(
        a,
        doc,
        vendor=vendor,
        reason=reason,
        gross_amount=Decimal(gross),
        deductible_amount=Decimal("1.00"),
    )
    sentinels = [vendor, reason, first, last, filename, gross, deductible, bad, tag]
    if Decimal(deductible) > Decimal(gross):
        deductible = gross

    errors: list[str] = []
    ok = await t.patch(
        a, item.id, {"version": 1, "deductible_amount": deductible, "person_id": str(person.id)}
    )
    assert ok.status_code == 200, ok.text
    invalid = await t.patch(a, item.id, {"version": 2, "gross_amount": bad.replace(".", ",")})
    assert invalid.status_code == 422
    errors.append(invalid.text)
    too_big = await t.patch(a, item.id, {"version": 2, "deductible_amount": f"9{gross}"})
    assert too_big.status_code == 422
    errors.append(too_big.text)
    conflict = await t.patch(a, item.id, {"version": 1, "deductible_amount": "2.00"})
    assert conflict.status_code == 409

    busy_doc = await t.doc(a, status=DocumentStatus.PROCESSING, original_filename=filename)
    busy_item = await t.item(a, busy_doc, vendor=vendor, reason=reason)
    busy = await t.patch(a, busy_item.id, {"version": 1, "gross_amount": gross})
    assert busy.status_code == 409
    errors.append(busy.text)
    gone = await t.api.request("DELETE", f"/documents/{doc.id}", cookie=a.cookie)
    assert gone.status_code == 204

    logs = json_log.getvalue()
    assert '"tax_item.updated"' in logs and '"tax_item.rejected"' in logs
    assert '"tax_item.version_conflict"' in logs
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
    assert metric_values
    for sentinel in sentinels:
        assert sentinel not in logs, sentinel
        assert not [v for v in span_values if sentinel in v], sentinel
        assert not [v for v in metric_values if sentinel in v], sentinel
        for body in errors:
            assert sentinel not in body, sentinel
