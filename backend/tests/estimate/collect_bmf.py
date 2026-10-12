"""Fill `scenarios/bmf_inputs_{year}.yaml` from the BMF Einkommensteuerrechner (not run in CI).

    uv sync --group golden && uv run playwright install chromium
    uv run python -m tests.estimate.collect_bmf --year 2025

Reuses `tests.tax.golden.collect` (headless Chromium, 2 s throttle, only the BMF host,
synthetic zvE values only) and rewrites the `expected` lines and `source.fetched_at`.
"""

from __future__ import annotations

import argparse
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from tests.tax.golden.collect import collect

SCENARIOS = Path(__file__).resolve().parent / "scenarios"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--year", type=int, required=True)
    year = parser.parse_args().year
    path = SCENARIOS / f"bmf_inputs_{year}.yaml"
    text = path.read_text(encoding="utf-8")
    cases = yaml.safe_load(text)["cases"]
    results = collect(year, cases)
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if m := re.match(r"\s*- \{id: (\w+),", line):
            est, soli = results[m.group(1)]
            value = f'expected: {{est: "{est}", soli: "{soli}"}}'
            lines[i] = re.sub(r"expected: (null|\{[^}]*\})\}$", value + "}", line)
        elif re.match(r"  fetched_at:", line):
            fetched = datetime.now(ZoneInfo("Europe/Berlin")).date().isoformat()
            lines[i] = f'  fetched_at: "{fetched}"'
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {len(results)} cases to {path.name}")


if __name__ == "__main__":
    main()
