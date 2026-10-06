# Labelling guide (label schema v1)

One document = at most one expected tax item. Vocabulary: `Category`, `CategoryGroup`,
`DocType`, `PaymentMethod` from `app/domain/enums.py`. Labels state what the **document**
supports before the tax engine applies caps, Pauschalen or percentages (#10 onwards).

## Fields (`label.yaml`)

| Field | Definition |
|---|---|
| `schema_version` | `1` |
| `id` | equals the directory name, `^[a-z0-9][a-z0-9-]{2,63}$` |
| `source` | `synthetic` (all of `bills_v0`), `anonymised_real` (#39, local only), `override_export` (#23, local only) |
| `file`, `variant` | `document.pdf` for `pdf_text` / `pdf_scanned`, `document.jpg` for `photo_jpeg`, `document.png` for `png` |
| `tags` | edge-case tags (below) |
| `expected.doc_type` | `generic_bill` for every bill, receipt, statement or certificate that yields a tax item; the official types for LStB, JStB, Kindergeld-, Elterngeld-, ALG-Bescheid |
| `expected.tax_relevant` | the document matters for the return. `false` ⇒ `deductible_amount: "0.00"` |
| `expected.category` | the tax item's category; `irrelevant` for private costs; `null` only for LStB, Kindergeld-, Elterngeld- and ALG-Bescheid (their data is an `official_record`, #18) |
| `expected.gross_amount` | total printed on the document (incl. VAT), quoted, exactly 2 decimals, negative for credit notes; `null` if none (official records) |
| `expected.deductible_amount` | the part that counts (before engine caps / Pauschalen); `"0.00"` if not relevant |
| `expected.labour_share_35a` | §35a group only: labour + travel + machine costs incl. VAT, as printed; else `null` |
| `expected.invoice_date` | issue date printed on the document (Rechnungs-, Bescheinigungs-, Bescheiddatum); `null` if none |
| `expected.payment_date` | date of payment if visible (Zahlungseingang, Lastschrift, Kassenbon date, Tag der Zuwendung); for annual statements the last payment date printed; `null` if not visible |
| `expected.tax_year` | 2025 or 2026: by `payment_date` (Abfluss-Prinzip, §11 EStG), else `invoice_date`; for annual certificates (LStB, JStB, Bescheide) the year the certificate covers |
| `expected.payment_method` | method visible on the document (paid or requested); `unknown` if none |
| `expected.vendor` | issuer as printed (fictional) |
| `expected.person_hint` | invoice recipient as printed, or `null` (receipts) |
| `notes` | optional, the labeller's reason |

## Decision table per `CategoryGroup`

| Group | Category | Typical Beleg (examples) | Counts as deductible |
|---|---|---|---|
| werbungskosten | `wk_arbeitsmittel` | Notebook, Fachbuch, Werkzeug für den Beruf | gross (GWG / AfA is the engine's job) |
| | `wk_fortbildung` | Seminar, Sprachkurs, Online-Kurs (beruflich) | gross |
| | `wk_fahrtkosten` | Bahnticket Dienstreise (nicht erstattet) | gross |
| | `wk_homeoffice` | exempt: Pauschale without a Beleg (profile, #13) | – |
| | `wk_arbeitszimmer` | Ausstattung / Kosten des häuslichen Arbeitszimmers | gross |
| | `wk_bewerbung` | Bewerbungsfotos, Mappen, Porto | gross |
| | `wk_kontofuehrung` | Kontoführungsentgelt (Kontoauszug) | fee as printed (Pauschale: engine) |
| | `wk_berufsverband` | Gewerkschafts- / Verbandsbeitrag | gross |
| | `wk_doppelte_haushaltsfuehrung` | Miete Zweitwohnung am Beschäftigungsort | gross (cap: engine) |
| | `steuerberatung` | Steuerberaterrechnung | work-related part only (`steuerberatung_split`) |
| vorsorge | `vorsorge_kv_pv` | Beitragsbescheinigung Kranken- / Pflegeversicherung | Basisabsicherung as printed |
| | `vorsorge_rv` | freiwillige Rentenversicherungsbeiträge | gross |
| | `vorsorge_riester` | Bescheinigung nach § 92 EStG | Eigenbeiträge (no Zulagen) |
| | `vorsorge_ruerup` | Basisrentenvertrag | gross |
| | `vorsorge_sonstige` | Haftpflicht, Unfall, BU | gross |
| sonderausgaben | `spenden` | Zuwendungsbestätigung (Geld / Sach), Kontoauszug ≤ 300 € | gross |
| | `kirchensteuer` | Kirchensteuer-Nachzahlung / Kirchgeld | gross |
| kind | `kinderbetreuung` | Kita, Tagesmutter (care only, not meals); refunds negative | care part |
| | `schulgeld` | Privatschule (without meals) | Schulgeld part (30 % rule: engine) |
| agb | `krankheitskosten` | Arzt, Zahnarzt, Brille, Physiotherapie, Rezept-Zuzahlung | prescribed / medical part |
| | `pflege` | Pflegedienst, Pflegeheim (Eigenanteil) | gross |
| | `behinderung` | behinderungsbedingte Kosten (Fahrdienst, Umbau) | gross |
| haushaltsnahe | `haushaltsnahe_dienstleistung` | Reinigung, Haushaltshilfe, Gartenpflege, Winterdienst | `labour_share_35a` |
| | `handwerkerleistung` | Maler, Sanitär, Heizungswartung, Schornsteinfeger | `labour_share_35a` |
| vermietung | `v_afa` | exempt: computed from property data (#13 / #20) | – |
| | `v_schuldzinsen` | Zinsbescheinigung Darlehen vermietete Wohnung | interest |
| | `v_erhaltung` | Reparatur / Sanierung der vermieteten Wohnung | gross |
| | `v_nebenkosten` | Grundsteuer, Gebäudeversicherung (vermietet) | gross |
| kapital | `kapital_bescheinigung` | Jahressteuerbescheinigung (`doc_type: jahressteuerbescheinigung`) | `"0.00"`; gross = Kapitalerträge |
| irrelevant | `irrelevant` | Lebensmittel, Restaurant, Kleidung, private Elektronik, privates Tanken, Kino, Spielzeug, Parken, Streaming | `"0.00"`, `tax_relevant: false` |
| (official) | `null` | LStB, Kindergeld-, Elterngeld-, ALG-Bescheid: `tax_relevant: true`, `deductible_amount: "0.00"`, `gross_amount: null` | tests `doc_type` routing only |

## Edge-case rules and tags

| Tag | Rule |
|---|---|
| `cash_35a` | §35a needs a cashless payment: a Handwerker invoice paid in cash keeps `category: handwerkerleistung` and its `labour_share_35a`, but `tax_relevant: false`, `deductible_amount: "0.00"` |
| `material_and_labour` | §35a counts only labour, travel and machine costs (incl. VAT), never material: `deductible_amount = labour_share_35a` |
| `mixed_receipt` | only the prescribed items of a drugstore / pharmacy receipt count (OTC medicine needs a prescription); cosmetics do not |
| `abfluss_year_boundary` | Abfluss-Prinzip: invoice 2025-12-18, paid 2026-01-08 → `tax_year: 2026`. Note: regular recurring payments within 10 days of the year end belong to the year they are economically due (10-Tage-Regel); the labels here do not use that case |
| `credit_note` | refunds are negative (`gross_amount` and `deductible_amount`), e.g. a Kita fee refund |
| `multi_page` | the total appears on the last page only; `gross_amount` is that total |
| `english_language` | English documents are labelled like German ones |
| `duplicate_rephotographed` | the same bill as `pdf_text` and `photo_jpeg`: two cases with identical `expected` |
| `steuerberatung_split` | only the work-related part of a tax adviser's invoice counts (`deductible_amount`), `gross_amount` is the full invoice |
| `no_payment_date` | no visible payment: `payment_date: null`, `tax_year` from `invoice_date`, `payment_method` as requested on the invoice |

Further rules:

- **Kinderbetreuung** covers care only, not meals (Verpflegung) or tuition.
- **Spenden**: up to 300 € a bank statement is enough (vereinfachter Nachweis); above that a
  Zuwendungsbestätigung is needed. Membership fees of sports clubs are not donations (trap case).
- **§35a** only for work in an existing household and only with cashless payment; a new build
  is not §35a (trap case, `category: irrelevant`).
- **Annual statements** (Beitragsbescheinigungen, Zinsbescheinigungen, Kita-Jahresbescheinigung):
  `invoice_date` = statement date, `payment_date` = last payment printed, so `tax_year` is the
  year paid.
- **person_hint** is the printed recipient (`Alex / Sam / Kim Muster`), not the person the cost
  is for (e.g. the Kita statement goes to Sam for Kim).
- Everything is fictional: "Muster" people, "Beispiel" / "Muster" vendors, Musterstadt
  addresses, Steuer-ID printed as `XX XXX XXX XXX`, IBAN as `DE00 XXXX XXXX XXXX XXXX 00`, and
  every page carries `Synthetisches Testdokument – belegbot evals`.
