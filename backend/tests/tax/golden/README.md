# Golden tariff values (#14)

`tariff_{year}.yaml` holds the BMF Einkommensteuerrechner's results for a fixed set of
synthetic zvE inputs. `tests/tax/test_golden_tariff.py` compares the tariff core with them
at **tolerance 0** (ESt to the euro, Soli to the cent). Expected values are only ever read
from the calculator, never computed by the engine; if the engine disagrees, fix the engine
or the params, never `expected`.

- Calculator: <https://www.bmf-steuerrechner.de/ekst/eingabeformekst.xhtml>
  ("Berechnung der Einkommensteuer"; tariff § 32a EStG on the zvE only)
- It shows **Einkommensteuer** and **Solidaritätszuschlag**, not Kirchensteuer. So `expected`
  has `est` and `soli`; Kirchensteuer (rate × ESt, down to cents) is covered by unit tests in
  `test_tariff.py`, and the church cases (`*_church_*`) still pin ESt and Soli. The runner
  compares `kist` too if a case ever carries it.
- No case was moved to unit tests: the calculator accepts all 26 inputs (incl. 0 and
  10 000 000). Negative zvE, zvE with cents and 10^9 are not golden cases; they are unit
  tests.

## Collect / refresh (script)

```bash
cd backend
uv sync --group golden            # Playwright, optional group (CI and plain `uv sync` skip it)
uv run playwright install chromium
# Linux without browser libraries (e.g. the devcontainer): uv run playwright install-deps chromium
uv run python -m tests.tax.golden.collect --year 2025
uv run python -m tests.tax.golden.collect --year 2026
uv run pytest tests/tax
```

The script (`collect.py`) runs headless Chromium, waits at least 2 s between requests
(form load, submit), aborts requests to any other host plus images / fonts / stylesheets,
uses no login and no personal data. It rewrites only the `expected:` lines and
`source.fetched_at` / `source.method` in place, so case order and comments are kept.

**Re-verify:** run it again on unchanged inputs; `git diff` must show nothing but
`fetched_at` (nothing at all on the same day). Any other change means the calculator or
the law changed: investigate before committing.

To add a case, append it with `expected: null` and run the script.

## Manual fallback

If the page changed and the script breaks, or no headless browser is available, a person
(or an agent with Claude-in-Chrome) fills each case by hand on the same page and edits the
YAML, setting `source.method: manual` and `source.fetched_at` to today (Europe/Berlin).

| Case input | Form field |
|---|---|
| `zve` | "zu versteuerndes Einkommen (zvE)" (`bmf_form_ekst:ekst_zve`), whole euros |
| `filing: single` | "Persönliche Verhältnisse: alleinstehend" (`ekst_pv` = `false`) |
| `filing: joint` | "verheiratet / verpartnert" (`ekst_pv` = `true`) |
| file year | "Berechnungsjahr" (`bmf_form_ekst:ekst_bj`) |
| `church_state` | not on the form (the calculator has no Kirchensteuer) |

Click "Berechnen" and copy the "Betrag" of the rows "Einkommensteuer" (to `est`, without
the `,00`) and "Solidaritätszuschlag" (to `soli`, with cents, `.` as decimal separator).

## Progressionsvorbehalt building blocks (#86)

The BMF calculator has no Progressionsvorbehalt input, so `blocks_{year}.yaml` lists the zvE
values the cases of `reference/progressionsvorbehalt_{year}.yaml` feed into `income_tax`
(`zvE + Lohnersatz`, and the plain zvE). `tests/tax/test_progression.py` asserts engine = BMF at
tolerance 0 for every block with a `bmf` value; a block with `bmf: null` is `blocks: pending`
(`xfail(strict)`, it turns red once all values are in, then remove the marker). Fill `bmf` with
the same Playwright procedure as above (2 s throttle, one value at a time, by hand or by
extending `collect.py`). The composition (rate, flooring, Pauschbetrag) is covered by the
hand-computed reference cases only.
