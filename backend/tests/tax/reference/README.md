# Reference cases for the deduction rules (#15)

No official calculator covers the rules in `app/tax/deductions/` (the BMF Einkommensteuerrechner
takes the zvE only, see `tests/tax/golden/README.md`). The cases here are the golden tests of
those rules (CLAUDE.md, "Deductions"): hand-computed, one file per group and year
(`werbungskosten_{year}.yaml`, `sonderausgaben_{year}.yaml`).

Each case has `id`, `rule` (W1 to W6, S1 to S5 of the issue), `source` (paragraph), `input`,
`arithmetic` (the calculation as text) and `expected`. The expected values were computed by hand
from the law text, never with the engine. `test_reference.py` fails (never skips) when a case
lacks `source`, `arithmetic` or `expected`, or when a supported year has no file.

A case may only be changed together with its `arithmetic`; a rule change needs a new or changed
case in every year it applies to (2026 differs from 2025 in the Entfernungspauschale and in
§ 9a Satz 3, Gewerkschaftsbeiträge beside the Pauschbetrag).

## What this proves and what not

The cases prove that the code implements our reading of the law text; they do not prove the
reading. The independent check is a re-derivation with exact `Fraction` from the law text and the
per-value source table in the PR. Not verifiable without outside help: the Finanzverwaltung's own
rounding (cents or euros), whether two employments on one day both count for the
Entfernungspauschale, and whether § 9a Satz 2 caps the Pauschbetrag at the wage (the law says it
does; that limit is applied by #17, not here).

Persons in the YAML: `A` taxpayer, `B` spouse (joint), `K1` / `K2` children, `X` an adult outside
the return, `null` no person; the key `-` stands for the bucket of items without a child.

## Vorsorge (#16)

`vorsorge_{year}.yaml`: rules A1 (Altersvorsorge), K1 / K2 (Kranken- und Pflegeversicherung,
single / joint), A3 (total). Amounts per person (`A`, `B`) as `VorsorgeInput` fields; `expected`
holds the `abzug` of the Altersvorsorge, of the Kranken-/Pflege part, the `total` and the note
codes. Höchstbetrag H = 29 344 (2025) / 30 826 (2026), read in the SVBezGrV and the
Beitragssatz-Bekanntmachung (see the params `source`). Nothing here is checked against an
official calculator (none exists); the cases prove our reading of § 10 EStG.
