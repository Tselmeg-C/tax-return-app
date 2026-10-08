"""The only reader of `app/tax/params/{year}.yaml` (I/O stays out of the pure `app.tax`).

Callers do `load_params(year)` and pass the `TaxParams` into the pure functions.
"""

from __future__ import annotations

import functools
from collections.abc import Mapping
from pathlib import Path

import yaml
from pydantic import ValidationError

from app.domain.enums import Category
from app.tax.models import MappingEntry, TaxParams

PARAMS_DIR = Path(__file__).resolve().parent / "tax" / "params"


class _UniqueKeyLoader(yaml.SafeLoader):
    """SafeLoader that rejects a key given twice in one mapping (e.g. a category, #9)."""


def _construct_mapping(loader: yaml.SafeLoader, node: yaml.MappingNode) -> dict[object, object]:
    seen: set[object] = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node)
        if key in seen:
            raise TaxParamsError(f"duplicate key {key!r}")
        seen.add(key)
    return loader.construct_mapping(node)


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_mapping,
)


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
    try:
        raw = yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)  # noqa: S506
    except TaxParamsError as exc:
        raise TaxParamsError(f"{path.name}: {exc}") from None
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


def mapping_table() -> dict[int, Mapping[Category, MappingEntry]]:
    """`{year: mapping}` for every supported year (#9's `MappingTable`)."""
    return {year: load_params(year).mapping for year in supported_years()}
