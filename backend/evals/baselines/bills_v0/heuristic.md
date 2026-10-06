### Eval bills_v0 · heuristic (heuristic-keywords) · NOT GATED
60 cases · dataset f0b28fa2 · git 3357455 · 2026-10-06T07:06Z · thresholds: proposed

| Metric | Value | Threshold | Baseline | Δ | Result |
|---|---|---|---|---|---|
| Relevance precision | 0.9565 | ≥ 0.95 |  |  | pass |
| Relevance recall | 0.4583 | ≥ 0.9 |  |  | fail |
| Relevance F1 | 0.6197 |  |  |  |  |
| Doc type accuracy | 0.9667 | ≥ 0.9 |  |  | pass |
| Category accuracy | 0.5179 | ≥ 0.85 |  |  | fail |
| Category group accuracy | 0.5179 |  |  |  |  |
| Gross exact match | 0.3571 | ≥ 0.9 |  |  | fail |
| Deductible exact match | 0.4833 | ≥ 0.85 |  |  | fail |
| Deductible abs error € (mean) | 466.55 | ≤ 5.0 |  |  | fail |
| Deductible abs error € (max) | 5400.00 |  |  |  |  |
| Deductible abs error € (sum) | 27992.70 |  |  |  |  |
| Overclaim € (sum) | 4620.00 |  |  |  |  |
| Underclaim € (sum) | 23372.70 |  |  |  |  |
| §35a labour share abs error € (mean) | 109.69 | ≤ 10.0 |  |  | fail |
| Tax year accuracy | 0.3833 | ≥ 0.95 |  |  | fail |
| Payment method accuracy | 0.3833 |  |  |  |  |
| Invoice date exact match | 0.3833 |  |  |  |  |
| Vendor match (report only) | 0.4000 |  |  |  |  |
| Person hint match (report only) | 0.0000 |  |  |  |  |
| Errors | 0 |  |  |  |  |
| Error rate | 0.0000 | ≤ 0.02 |  |  | pass |
| Cost € (total) | n/a |  |  |  |  |
| Cost € per doc (mean) | n/a | ≤ 0.02 (optional) |  |  | n/a |
| Cost € per doc (p95) | n/a |  |  |  |  |
| Input tokens | n/a |  |  |  |  |
| Output tokens | n/a |  |  |  |  |
| Latency ms (p50) | 2 |  |  |  |  |
| Latency ms (p95) | 7 | ≤ 30000 (optional) |  |  | pass |

**Per category group**

| Group | Cases | Category accuracy |
|---|---|---|
| werbungskosten | 12 | 0.4167 |
| vorsorge | 6 | 0.3333 |
| sonderausgaben | 4 | 0.5000 |
| kind | 4 | 0.5000 |
| agb | 6 | 0.3333 |
| haushaltsnahe | 8 | 0.5000 |
| vermietung | 4 | 0.2500 |
| kapital | 1 | 1.0000 |
| irrelevant | 11 | 0.9091 |

**Top confusions**

| Expected | Predicted | Count |
|---|---|---|
| krankheitskosten | irrelevant | 3 |
| handwerkerleistung | irrelevant | 2 |
| haushaltsnahe_dienstleistung | irrelevant | 2 |
| spenden | irrelevant | 2 |
| v_nebenkosten | irrelevant | 2 |
| wk_fortbildung | irrelevant | 2 |
| behinderung | irrelevant | 1 |
| irrelevant | handwerkerleistung | 1 |
| kinderbetreuung | irrelevant | 1 |
| schulgeld | irrelevant | 1 |

**Failing cases** (45, first 20)

- `b002-arbeitsmittel-notebook-foto` [duplicate_rephotographed]: tax_relevant, category, category_group, gross_amount, deductible_amount, tax_year, payment_method, invoice_date
- `b003-fortbildung-seminar`: tax_relevant, category, category_group, gross_amount, deductible_amount, tax_year, payment_method, invoice_date
- `b004-fortbildung-online-course-en` [english_language]: gross_amount, deductible_amount, tax_year, invoice_date
- `b005-fortbildung-sprachkurs`: tax_relevant, category, category_group, gross_amount, deductible_amount, tax_year, payment_method, invoice_date
- `b006-fahrtkosten-dienstreise`: tax_relevant, category, category_group, gross_amount, deductible_amount, tax_year, payment_method, invoice_date
- `b008-bewerbung-fotos`: tax_relevant, category, category_group, gross_amount, deductible_amount, tax_year, payment_method, invoice_date
- `b009-kontofuehrung`: gross_amount, deductible_amount, payment_method
- `b010-berufsverband-gewerkschaft`: tax_relevant, category, category_group, gross_amount, deductible_amount, tax_year, payment_method, invoice_date
- `b011-doppelte-haushaltsfuehrung`: tax_relevant, category, category_group, gross_amount, deductible_amount, tax_year, payment_method, invoice_date
- `b012-steuerberatung-split` [steuerberatung_split]: deductible_amount
- `b014-kv-pv-privat`: tax_relevant, category, category_group, gross_amount, deductible_amount, tax_year, payment_method, invoice_date
- `b015-rv-freiwillig`: tax_relevant, category, category_group, gross_amount, deductible_amount, tax_year, payment_method, invoice_date
- `b017-ruerup`: tax_relevant, category, category_group, gross_amount, deductible_amount, tax_year, payment_method, invoice_date
- `b018-haftpflicht`: tax_relevant, category, category_group, gross_amount, deductible_amount, tax_year, payment_method, invoice_date
- `b019-spende-zuwendungsbestaetigung`: gross_amount, deductible_amount, payment_method, invoice_date
- `b020-spende-kontoauszug`: tax_relevant, category, category_group, gross_amount, deductible_amount, tax_year, payment_method, invoice_date
- `b021-sachspende`: tax_relevant, category, category_group, gross_amount, deductible_amount, tax_year, payment_method, invoice_date
- `b022-kirchensteuer-nachzahlung`: gross_amount, deductible_amount
- `b023-kita-jahresbescheinigung`: deductible_amount
- `b024-tagesmutter`: tax_relevant, category, category_group, gross_amount, deductible_amount, tax_year, payment_method, invoice_date
