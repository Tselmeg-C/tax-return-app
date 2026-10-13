"""`GET /summary?year=YYYY` (#17): the refund estimate of the signed-in household.

All numbers come from `app.estimate.service.build_summary` (shared with the bot). Money is a
dot-decimal string, codes carry no values; an unsupported year is `422 unsupported_year`.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.api.deps import Scope, SignedIn
from app.auth.clock import Clock
from app.estimate.service import build_summary
from app.tax_params import UnsupportedTaxYear

router = APIRouter()
BERLIN = ZoneInfo("Europe/Berlin")


def _today(request: Request) -> date:
    clock: Clock = request.app.state.clock
    return clock.now().astimezone(BERLIN).date()


@router.get("/summary")
async def get_summary(request: Request, user: SignedIn, scope: Scope, year: int) -> JSONResponse:
    try:
        result: dict[str, Any] = await build_summary(scope, year, _today(request))
    except UnsupportedTaxYear:
        return JSONResponse({"detail": "unsupported_year", "field": "year"}, status_code=422)
    return JSONResponse(result)
