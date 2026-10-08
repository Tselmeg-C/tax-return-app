### Eval bills_v0 · replay (openai, classify=openai:gpt-4.1-mini,extract=openai:gpt-4.1, prompt v2, recording pipeline-openai-v2) · NOT GATED
60 cases · dataset 5945bebd · git 6784944 · 2026-10-07T14:04Z

| Metric | Value | Threshold | Baseline | Δ | Result |
|---|---|---|---|---|---|
| Relevance precision | 1.0000 | ≥ 0.95 |  |  | pass |
| Relevance recall | 1.0000 | ≥ 0.9 |  |  | pass |
| Relevance F1 | 1.0000 |  |  |  |  |
| Doc type accuracy | 0.9833 | ≥ 0.9 |  |  | pass |
| Category accuracy | 0.9821 | ≥ 0.85 |  |  | pass |
| Category group accuracy | 0.9821 |  |  |  |  |
| Gross exact match | 0.9643 | ≥ 0.9 |  |  | pass |
| Deductible exact match | 0.9833 | ≥ 0.85 |  |  | pass |
| Deductible abs error € (mean) | 5.00 | ≤ 5.0 |  |  | pass |
| Deductible abs error € (max) | 300.00 |  |  |  |  |
| Deductible abs error € (sum) | 300.00 |  |  |  |  |
| Overclaim € (sum) | 0.00 |  |  |  |  |
| Underclaim € (sum) | 300.00 |  |  |  |  |
| §35a labour share abs error € (mean) | 0.00 | ≤ 10.0 |  |  | pass |
| Tax year accuracy | 1.0000 | ≥ 0.95 |  |  | pass |
| Payment method accuracy | 0.8833 |  |  |  |  |
| Invoice date exact match | 1.0000 |  |  |  |  |
| Vendor match (report only) | 0.8833 |  |  |  |  |
| Person hint match (report only) | 0.8302 |  |  |  |  |
| Errors | 0 |  |  |  |  |
| Error rate | 0.0000 | ≤ 0.02 |  |  | pass |
| Cost € (total) | 0.4241 |  |  |  |  |
| Cost € per doc (mean) | 0.0071 | ≤ 0.02 (optional) |  |  | pass |
| Cost € per doc (p95) | 0.0105 |  |  |  |  |
| Input tokens | 388007 |  |  |  |  |
| Output tokens | 16583 |  |  |  |  |
| Latency ms (p50) | 4894 |  |  |  |  |
| Latency ms (p95) | 17779 | ≤ 30000 (optional) |  |  | pass |

**Per category group**

| Group | Cases | Category accuracy |
|---|---|---|
| werbungskosten | 12 | 1.0000 |
| vorsorge | 6 | 1.0000 |
| sonderausgaben | 4 | 1.0000 |
| kind | 4 | 1.0000 |
| agb | 6 | 1.0000 |
| haushaltsnahe | 8 | 1.0000 |
| vermietung | 4 | 1.0000 |
| kapital | 1 | 0.0000 |
| irrelevant | 11 | 1.0000 |

**Top confusions**

| Expected | Predicted | Count |
|---|---|---|
| kapital_bescheinigung | none | 1 |

**Failing cases** (10, first 20)

- `b017-ruerup`: doc_type
- `b020-spende-kontoauszug`: gross_amount, deductible_amount
- `b044-jahressteuerbescheinigung`: category, category_group, gross_amount
- `b045-supermarkt-lebensmittel`: payment_method
- `b046-restaurant`: payment_method
- `b047-kleidung`: payment_method
- `b048-elektronik-privat`: payment_method
- `b050-kino`: payment_method
- `b051-spielzeug`: payment_method
- `b055-streaming-abo`: payment_method
