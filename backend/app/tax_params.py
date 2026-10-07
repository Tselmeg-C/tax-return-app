"""The only reader of `app/tax/params/{year}.yaml` (I/O stays out of the pure `app.tax`).

Callers do `load_params(year)` and pass the `TaxParams` into the pure functions.
"""

from __future__ import annotations

import functools
from pathlib import Path

import yaml
from pydantic import ValidationError

from app.tax.models import TaxParams

PARAMS_DIR = Path(__file__).resolve().parent / "tax" / "params"


class TaxParamsError(ValueError):
    """A params file is missing a key, has an unknown or unquoted one, or is inconsistent."""


class UnsupportedTaxYear(LookupError):
    pass


def supported_years() -> tuple[int, ...]:
    return tuple(sorted(int(p.stem) for p in PARAMS_DIR.glob("*.yaml") if p.stem.isdigit()))


@functools.cache
def load_params(year: int) -> TaxParams:
    years = supported_years()
    if year not in years:
        listed = ", ".join(str(y) for y in years)
        raise UnsupportedTaxYear(f"tax year {year} is not supported (supported: {listed})")
    return parse_params_file(PARAMS_DIR / f"{year}.yaml")


def parse_params_file(path: Path) -> TaxParams:
    """Parse and validate one params file (uncached; `load_params` is the cached entry)."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise TaxParamsError(f"{path.name}: top level must be a mapping")
    if raw.get("year") != int(path.stem):
        raise TaxParamsError(f"{path.name}: year: {raw.get('year')!r} does not match file name")
    try:
        return TaxParams.model_validate(raw)
    except ValidationError as exc:
        err = exc.errors()[0]
        key = ".".join(str(part) for part in err["loc"])
        kind = {"missing": "missing", "extra_forbidden": "unknown key"}.get(err["type"])
        raise TaxParamsError(f"{path.name}: {key}: {kind or err['msg']}") from None
