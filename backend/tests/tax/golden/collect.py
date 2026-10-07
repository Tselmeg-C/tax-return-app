"""Collect golden tariff values from the BMF Einkommensteuerrechner (not run in CI).

    uv sync --group golden && uv run playwright install chromium
    uv run python -m tests.tax.golden.collect --year 2025

Reads `tariff_{year}.yaml`, fills the BMF form per case (zvE, alleinstehend / verheiratet,
Berechnungsjahr), reads Einkommensteuer and Solidaritätszuschlag from the result table and
writes `expected` plus `source.fetched_at` / `source.method` back into the same file, line by
line (case order and comments kept). Headless Chromium, at most one request every 2 s,
requests to any other host
(and images, fonts, stylesheets) are aborted. Inputs are synthetic zvE values only.
"""

from __future__ import annotations

import argparse
import re
import time
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

GOLDEN_DIR = Path(__file__).resolve().parent
FORM_URL = "https://www.bmf-steuerrechner.de/ekst/eingabeformekst.xhtml"
HOST = "www.bmf-steuerrechner.de"
THROTTLE_S = 2.0
SKIPPED = {"image", "font", "stylesheet", "media"}  # not needed to read the result


def parse_euro(text: str) -> Decimal:
    """'1.325,42 Euro' -> Decimal('1325.42')."""
    match = re.fullmatch(r"([\d.]+,\d{2}) Euro", text.strip())
    if not match:
        raise ValueError(f"unexpected amount: {text!r}")
    return Decimal(match.group(1).replace(".", "").replace(",", "."))


def update_file(text: str, results: dict[str, tuple[Decimal, Decimal]], fetched_at: str) -> str:
    """Rewrite the `expected` lines of the cases in `results` and the source block."""
    lines = text.splitlines()
    case_id = None
    for i, line in enumerate(lines):
        if m := re.match(r"\s*- id: (\S+)", line):
            case_id = m.group(1)
        elif line.strip().startswith("expected:") and case_id in results:
            est, soli = results[case_id]
            indent = line[: len(line) - len(line.lstrip())]
            lines[i] = f'{indent}expected: {{est: "{est}", soli: "{soli}"}}'
        elif re.match(r"  fetched_at:", line):
            lines[i] = f'  fetched_at: "{fetched_at}"'
        elif re.match(r"  method:", line):
            lines[i] = "  method: script"
    return "\n".join(lines) + "\n"


class Throttle:
    def __init__(self) -> None:
        self._last = 0.0

    def wait(self) -> None:
        delay = self._last + THROTTLE_S - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        self._last = time.monotonic()


def collect(year: int, cases: list[dict[str, Any]]) -> dict[str, tuple[Decimal, Decimal]]:
    from playwright.sync_api import Route, sync_playwright  # lazy: optional `golden` group

    def only_bmf(route: Route) -> None:
        request = route.request
        if request.url.split("/")[2] == HOST and request.resource_type not in SKIPPED:
            route.continue_()
        else:
            route.abort()

    throttle = Throttle()
    results: dict[str, tuple[Decimal, Decimal]] = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.route("**/*", only_bmf)
        for case in cases:
            inp = case["input"]
            married = "true" if inp["filing"] == "joint" else "false"
            throttle.wait()
            page.goto(FORM_URL)
            page.fill("#bmf_form_ekst\\:ekst_zve", str(inp["zve"]))
            page.check(f"input[name='bmf_form_ekst:ekst_pv'][value='{married}']")
            page.select_option("#bmf_form_ekst\\:ekst_bj", str(year))
            throttle.wait()
            page.click("#bmf_form_ekst\\:income_ekst")
            page.wait_for_url("**ekst-result=true*")
            body = page.inner_text("body").replace("\u200b", "")
            if f"Ergebnis der Berechnung der Einkommensteuer {year}" not in body:
                raise RuntimeError(f"{case['id']}: no result for {year} (page changed?)")
            amounts: dict[str, Decimal] = {}
            for row in page.query_selector_all("table.table_ekst_result tr"):
                cells = row.inner_text().replace("\u200b", "").split("\t")
                if len(cells) >= 2 and cells[0] in ("Einkommensteuer", "Solidaritätszuschlag"):
                    amounts[cells[0]] = parse_euro(cells[1])
            est, soli = amounts["Einkommensteuer"], amounts["Solidaritätszuschlag"]
            if est != est.to_integral_value():
                raise RuntimeError(f"{case['id']}: ESt with cents: {est}")
            results[case["id"]] = (est.quantize(Decimal(1)), soli)
            print(f"{case['id']}: est {est} soli {soli}")
        browser.close()
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--year", type=int, required=True)
    args = parser.parse_args()
    path = GOLDEN_DIR / f"tariff_{args.year}.yaml"
    text = path.read_text(encoding="utf-8")
    cases = yaml.safe_load(text)["cases"]
    results = collect(args.year, cases)
    fetched_at = datetime.now(ZoneInfo("Europe/Berlin")).date().isoformat()
    path.write_text(update_file(text, results, fetched_at), encoding="utf-8")
    print(f"wrote {len(results)} cases to {path.name}")


if __name__ == "__main__":
    main()
