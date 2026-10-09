# ruff: noqa: E501
"""agb params (#76): values and the loader errors naming the key."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.tax_params import PARAMS_DIR, TaxParamsError, load_params, parse_params_file

D = Decimal


def _broken(tmp_path: Path, edit: Any) -> Path:
    data = yaml.safe_load((PARAMS_DIR / "2025.yaml").read_text(encoding="utf-8"))
    edit(data["agb"])
    path = tmp_path / "2025.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


@pytest.mark.parametrize("year", [2025, 2026])
def test_values(year: int) -> None:
    a = load_params(year).agb
    z = a.zumutbare_belastung
    assert (z.bracket1_upper, z.bracket2_upper) == (D(15340), D(51130))
    assert {k: tuple(str(r) for r in v) for k, v in z.rates.items()} == {
        "no_children_single": ("0.05", "0.06", "0.07"),
        "no_children_joint": ("0.04", "0.05", "0.06"),
        "one_or_two_children": ("0.02", "0.03", "0.04"),
        "three_plus_children": ("0.01", "0.01", "0.02"),
    }
    assert [int(v) for v in a.behinderten_pauschbetrag.values()] == [
        384, 620, 860, 1140, 1440, 1780, 2120, 2460, 2840
    ]  # fmt: skip
    assert list(a.behinderten_pauschbetrag) == [str(g) for g in range(20, 101, 10)]
    assert a.hilflos_blind == D(7400)
    assert "§ 33 Abs. 3" in a.source and "§ 33b Abs. 3" in a.source and "BGBl" in a.source
    assert not a.provisional  # every constant read in the law text, see the PR table


def _del(*path: str):  # noqa: ANN202
    def edit(agb: dict[str, Any]) -> None:
        for key in path[:-1]:
            agb = agb[key]
        del agb[path[-1]]

    return edit


def _set(*path_value: Any):  # noqa: ANN202
    *path, value = path_value

    def edit(agb: dict[str, Any]) -> None:
        for key in path[:-1]:
            agb = agb[key]
        agb[path[-1]] = value

    return edit


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (_del("zumutbare_belastung", "bracket2_upper"), "agb.zumutbare_belastung.bracket2_upper: missing"),
        (_set("zumutbare_belastung", "rates", "no_children_single", [0.05, "0.06", "0.07"]), "agb.zumutbare_belastung.rates.no_children_single.0"),
        (_set("zumutbare_belastung", "rates", "no_children_single", ["1.5", "0.06", "0.07"]), "rates.no_children_single[1] must be in (0, 1)"),
        (_set("zumutbare_belastung", "bracket1_upper", "60000"), "bracket1_upper must be positive and below bracket2_upper"),
        (_set("zumutbare_belastung", "bracket1_upper", "51130"), "bracket1_upper must be positive and below bracket2_upper"),
        (_set("behinderten_pauschbetrag", "25", "400"), "behinderten_pauschbetrag: grades must be 20 to 100"),
        (_del("behinderten_pauschbetrag", "20"), "behinderten_pauschbetrag: grades must be 20 to 100"),
        (_set("behinderten_pauschbetrag", "60", "2000"), "behinderten_pauschbetrag must be positive and ascending"),
        (_set("hilflos_blind", 7400), "agb.hilflos_blind"),
        (_del("source"), "agb.source: missing"),
        (_set("surprise", "1"), "agb.surprise: unknown key"),
    ],
)  # fmt: skip
def test_invalid(tmp_path: Path, edit: Any, message: str) -> None:
    with pytest.raises(TaxParamsError) as exc:
        parse_params_file(_broken(tmp_path, edit))
    assert message in str(exc.value) or message.split("agb.")[-1] in str(exc.value)


def test_missing_section(tmp_path: Path) -> None:
    data = yaml.safe_load((PARAMS_DIR / "2025.yaml").read_text(encoding="utf-8"))
    del data["agb"]
    path = tmp_path / "2025.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    with pytest.raises(TaxParamsError, match="agb: missing"):
        parse_params_file(path)
