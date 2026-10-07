"""`mapping` section of params/{year}.yaml and `app.tax.mapping` (#9): golden table + loader."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.domain.enums import Anlage, Category
from app.tax.mapping import map_category
from app.tax.models import MAPPED_CATEGORIES
from app.tax_params import PARAMS_DIR, TaxParamsError, mapping_table, parse_params_file

# Golden table: (anlage, zeile) per category, checked against the published 2025 forms
# (2026: copied, provisional). Changing a value here needs a source in the YAML.
GOLDEN: dict[str, tuple[str, str | None]] = {
    "wk_arbeitsmittel": ("n", "54"),
    "wk_fortbildung": ("n", "60"),
    "wk_fahrtkosten": ("n", "66"),
    "wk_homeoffice": ("n", "58"),
    "wk_arbeitszimmer": ("n", "57"),
    "wk_bewerbung": ("n", "62"),
    "wk_kontofuehrung": ("n", "62"),
    "wk_berufsverband": ("n", "53"),
    "wk_doppelte_haushaltsfuehrung": ("n", None),
    "steuerberatung": ("n", "62"),
    "vorsorge_kv_pv": ("vorsorgeaufwand", "16"),
    "vorsorge_rv": ("vorsorgeaufwand", "6"),
    "vorsorge_riester": ("av", None),
    "vorsorge_ruerup": ("vorsorgeaufwand", "8"),
    "vorsorge_sonstige": ("vorsorgeaufwand", "46"),
    "spenden": ("sonderausgaben", "5"),
    "kirchensteuer": ("sonderausgaben", "4"),
    "kinderbetreuung": ("kind", "67"),
    "schulgeld": ("kind", "56"),
    "krankheitskosten": ("agb", "24"),
    "pflege": ("agb", "27"),
    "behinderung": ("agb", "30"),
    "haushaltsnahe_dienstleistung": ("haushaltsnahe_aufwendungen", "5"),
    "handwerkerleistung": ("haushaltsnahe_aufwendungen", "6"),
    "v_afa": ("v", "33"),
    "v_schuldzinsen": ("v", "46"),
    "v_erhaltung": ("v", "55"),
    "v_nebenkosten": ("v", "73"),
    "kapital_bescheinigung": ("kap", None),
}


def test_golden_covers_every_category() -> None:
    assert len(GOLDEN) == 29
    assert set(GOLDEN) == {c.value for c in MAPPED_CATEGORIES}


@pytest.mark.parametrize("year", [2025, 2026])
@pytest.mark.parametrize("category", sorted(GOLDEN))
def test_golden_mapping(year: int, category: str) -> None:
    line = map_category(Category(category), year, mapping_table())
    anlage, zeile = GOLDEN[category]
    assert (line.anlage, line.zeile, line.supported) == (Anlage(anlage), zeile, True)


@pytest.mark.parametrize("year", [2025, 2026])
def test_entries_have_sources(year: int) -> None:
    for category, entry in mapping_table()[year].items():
        assert entry.source.strip(), category
        assert entry.provisional is (year == 2026)


def test_irrelevant_and_unsupported_year() -> None:
    table = mapping_table()
    assert map_category(Category.IRRELEVANT, 2025, table) == (
        map_category(Category.IRRELEVANT, 2026, table)
    )
    irrelevant = map_category(Category.IRRELEVANT, 2025, table)
    assert (irrelevant.anlage, irrelevant.zeile, irrelevant.supported) == (None, None, True)
    old = map_category(Category.SPENDEN, 2024, table)
    assert (old.anlage, old.zeile, old.supported) == (None, None, False)


def _write(tmp_path: Path, edit: Any) -> Path:
    data = yaml.safe_load((PARAMS_DIR / "2025.yaml").read_text(encoding="utf-8"))
    edit(data["mapping"])
    path = tmp_path / "2025.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (lambda m: m.__setitem__("wk_foo", m["spenden"]), "mapping.wk_foo"),
        (lambda m: m.pop("spenden"), "mapping: missing spenden"),
        (lambda m: m["spenden"].__setitem__("anlage", "foo"), "mapping.spenden.anlage"),
        (lambda m: m["spenden"].__setitem__("line", "5"), "mapping.spenden.line: unknown key"),
        (lambda m: m.__setitem__("irrelevant", m["spenden"]), "irrelevant must not be mapped"),
    ],
    ids=["unknown-category", "missing-category", "bad-anlage", "unknown-key", "irrelevant"],
)
def test_invalid_mapping_names_the_key(tmp_path: Path, edit: Any, message: str) -> None:
    with pytest.raises(TaxParamsError, match=re.escape(message)):
        parse_params_file(_write(tmp_path, edit))


def test_duplicate_category_is_rejected(tmp_path: Path) -> None:
    text = (PARAMS_DIR / "2025.yaml").read_text(encoding="utf-8")
    line = next(x for x in text.splitlines() if x.startswith("  spenden:"))
    path = tmp_path / "2025.yaml"
    path.write_text(text + line + "\n", encoding="utf-8")
    with pytest.raises(TaxParamsError, match="duplicate key 'spenden'"):
        parse_params_file(path)
