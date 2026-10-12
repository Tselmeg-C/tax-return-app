"""Domain enums shared by the DB models, the LLM schemas (#9) and the API.

Values are stable lowercase ASCII codes; they are stored in the DB (VARCHAR + CHECK) and,
from #9 on, are the vocabulary of the extraction schemas. Renaming or removing a value is
a migration, and for `Category` / `DocType` / `PaymentMethod` also an eval run
(`_docs/adlc.md`). German display labels live in `LABELS_DE`, never in the values.
"""

from __future__ import annotations

from enum import StrEnum


class UserRole(StrEnum):
    OWNER = "owner"
    MEMBER = "member"


class PersonKind(StrEnum):
    ADULT = "adult"
    CHILD = "child"


class Religion(StrEnum):
    """Church-tax membership (#13): `none` = no church-tax-levying community, `other` = another
    church-tax-levying community. The rate comes from params (`church_tax.rate_by_state`)."""

    NONE = "none"
    EV = "ev"
    RK = "rk"
    OTHER = "other"


class Channel(StrEnum):
    WEB = "web"
    TELEGRAM = "telegram"


class DocumentStatus(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    DONE = "done"
    NEEDS_ATTENTION = "needs_attention"
    FAILED = "failed"


class DocType(StrEnum):
    GENERIC_BILL = "generic_bill"
    LOHNSTEUERBESCHEINIGUNG = "lohnsteuerbescheinigung"
    JAHRESSTEUERBESCHEINIGUNG = "jahressteuerbescheinigung"
    NEBENKOSTENABRECHNUNG = "nebenkostenabrechnung"
    KINDERGELD_BESCHEID = "kindergeld_bescheid"
    ELTERNGELD_BESCHEID = "elterngeld_bescheid"
    ALG_BESCHEID = "alg_bescheid"
    OTHER = "other"


class JobKind(StrEnum):
    """Background job kinds (`job.kind`, #6)."""

    PROCESS_DOCUMENT = "process_document"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ExtractionStep(StrEnum):
    CLASSIFY = "classify"
    EXTRACT = "extract"


class Anlage(StrEnum):
    HAUPTVORDRUCK = "hauptvordruck"
    N = "n"
    VORSORGEAUFWAND = "vorsorgeaufwand"
    AV = "av"
    SONDERAUSGABEN = "sonderausgaben"
    AGB = "agb"
    HAUSHALTSNAHE_AUFWENDUNGEN = "haushaltsnahe_aufwendungen"
    KIND = "kind"
    KAP = "kap"
    V = "v"


class PaymentMethod(StrEnum):
    CASH = "cash"
    BANK_TRANSFER = "bank_transfer"
    DIRECT_DEBIT = "direct_debit"
    CARD = "card"
    PAYPAL = "paypal"
    OTHER = "other"
    UNKNOWN = "unknown"


class AttentionReason(StrEnum):
    """Why a document ended `needs_attention` (#9). Definition order = priority: when several
    reasons apply, the first one is stored in `document.attention_reason`."""

    CLASSIFICATION_FAILED = "classification_failed"
    EXTRACTION_FAILED = "extraction_failed"
    UNREADABLE = "unreadable"
    MULTIPLE_DOCUMENTS = "multiple_documents"
    DOC_TYPE_NOT_SUPPORTED = "doc_type_not_supported"
    POSSIBLE_DUPLICATE = "possible_duplicate"
    FOREIGN_CURRENCY = "foreign_currency"
    IMPLAUSIBLE_AMOUNT = "implausible_amount"
    IMPLAUSIBLE_DATE = "implausible_date"
    SUM_MISMATCH = "sum_mismatch"
    SIGN_MISMATCH = "sign_mismatch"
    CREDIT_NOTE_35A = "credit_note_35a"
    LABOUR_SHARE_MISSING = "labour_share_missing"
    PAYMENT_METHOD_UNKNOWN_35A = "payment_method_unknown_35a"
    MULTIPLE_CATEGORIES = "multiple_categories"
    ASSET_DEPRECIATION = "asset_depreciation"
    DATE_MISSING = "date_missing"
    UNSUPPORTED_YEAR = "unsupported_year"
    YEAR_BOUNDARY_RECURRING = "year_boundary_recurring"
    LOW_CONFIDENCE = "low_confidence"


class FilingStatus(StrEnum):
    """Tariff for the tax core (#14): Grundtarif or Splitting (§ 32a Abs. 5 EStG)."""

    SINGLE = "single"
    JOINT = "joint"


class DeductionNote(StrEnum):
    """Why the deduction engine (#15, `app/tax/deductions/`) excluded, clipped or assumed
    something. No DB column: results carry codes and ids only, never amounts or names."""

    PERSON_NOT_IN_RETURN = "person_not_in_return"
    PERSON_UNASSIGNED = "person_unassigned"
    NO_EMPLOYMENT = "no_employment"
    EXCLUDED_ATTENTION = "excluded_attention"
    INCLUDED_UNREVIEWED = "included_unreviewed"
    NET_NEGATIVE_CLIPPED = "net_negative_clipped"
    COVERED_BY_PAUSCHALE = "covered_by_pauschale"
    ARBEITSZIMMER_REPLACES_HOMEOFFICE = "arbeitszimmer_replaces_homeoffice"
    FAHRTKOSTEN_WITH_ENTFERNUNGSPAUSCHALE = "fahrtkosten_with_entfernungspauschale"
    COMMUTE_ASSUMES_CAR = "commute_assumes_car"
    DHF_UNCHECKED = "dhf_unchecked"
    CHILD_NOT_ELIGIBLE = "child_not_eligible"
    CHILD_AGE_REVIEW = "child_age_review"
    CHILD_UNASSIGNED = "child_unassigned"
    CASH_EXCLUDED = "cash_excluded"
    PAYMENT_UNVERIFIED = "payment_unverified"
    SPENDEN_OVER_LIMIT = "spenden_over_limit"
    LABOUR_SHARE_MISSING = "labour_share_missing"
    EMPLOYER_SHARE_ASSUMED = "employer_share_assumed"
    KV_PV_UNSPLIT = "kv_pv_unsplit"
    RIESTER_NOT_SUPPORTED = "riester_not_supported"
    CHILD_OVER_25_DISABLED = "child_over_25_disabled"


class Bundesland(StrEnum):
    """ISO 3166-2:DE codes, lowercased (#13). `.value.upper()` is the params' `rate_by_state`
    key."""

    BW = "bw"
    BY = "by"
    BE = "be"
    BB = "bb"
    HB = "hb"
    HH = "hh"
    HE = "he"
    MV = "mv"
    NI = "ni"
    NW = "nw"
    RP = "rp"
    SL = "sl"
    SN = "sn"
    ST = "st"
    SH = "sh"
    TH = "th"


class Steuerklasse(StrEnum):
    I = "1"  # noqa: E741
    II = "2"
    III = "3"
    IV = "4"
    V = "5"
    VI = "6"


class AllowanceShare(StrEnum):
    """Kinderfreibetrag + BEA: both halves (`full`) or one (`half`)."""

    FULL = "full"
    HALF = "half"


class AuditAction(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


class ActorType(StrEnum):
    USER = "user"
    SYSTEM = "system"


class CategoryGroup(StrEnum):
    WERBUNGSKOSTEN = "werbungskosten"
    VORSORGE = "vorsorge"
    SONDERAUSGABEN = "sonderausgaben"
    KIND = "kind"
    AGB = "agb"
    HAUSHALTSNAHE = "haushaltsnahe"
    VERMIETUNG = "vermietung"
    KAPITAL = "kapital"
    IRRELEVANT = "irrelevant"


class Category(StrEnum):
    """Tax item category (`plan.md` §7). `Category -> Anlage/Zeile` is per year in params (#9)."""

    # werbungskosten
    WK_ARBEITSMITTEL = "wk_arbeitsmittel"
    WK_FORTBILDUNG = "wk_fortbildung"
    WK_FAHRTKOSTEN = "wk_fahrtkosten"
    WK_HOMEOFFICE = "wk_homeoffice"
    WK_ARBEITSZIMMER = "wk_arbeitszimmer"
    WK_BEWERBUNG = "wk_bewerbung"
    WK_KONTOFUEHRUNG = "wk_kontofuehrung"
    WK_BERUFSVERBAND = "wk_berufsverband"
    WK_DOPPELTE_HAUSHALTSFUEHRUNG = "wk_doppelte_haushaltsfuehrung"
    STEUERBERATUNG = "steuerberatung"
    # vorsorge
    VORSORGE_KV_PV = "vorsorge_kv_pv"
    VORSORGE_RV = "vorsorge_rv"
    VORSORGE_RIESTER = "vorsorge_riester"
    VORSORGE_RUERUP = "vorsorge_ruerup"
    VORSORGE_SONSTIGE = "vorsorge_sonstige"
    # sonderausgaben
    SPENDEN = "spenden"
    KIRCHENSTEUER = "kirchensteuer"
    # kind
    KINDERBETREUUNG = "kinderbetreuung"
    SCHULGELD = "schulgeld"
    # agb (außergewöhnliche Belastungen)
    KRANKHEITSKOSTEN = "krankheitskosten"
    PFLEGE = "pflege"
    BEHINDERUNG = "behinderung"
    # haushaltsnahe (§35a)
    HAUSHALTSNAHE_DIENSTLEISTUNG = "haushaltsnahe_dienstleistung"
    HANDWERKERLEISTUNG = "handwerkerleistung"
    # vermietung (Anlage V)
    V_AFA = "v_afa"
    V_SCHULDZINSEN = "v_schuldzinsen"
    V_ERHALTUNG = "v_erhaltung"
    V_NEBENKOSTEN = "v_nebenkosten"
    # kapital
    KAPITAL_BESCHEINIGUNG = "kapital_bescheinigung"
    # irrelevant
    IRRELEVANT = "irrelevant"


_G = CategoryGroup
_C = Category

CATEGORY_GROUP: dict[Category, CategoryGroup] = {
    _C.WK_ARBEITSMITTEL: _G.WERBUNGSKOSTEN,
    _C.WK_FORTBILDUNG: _G.WERBUNGSKOSTEN,
    _C.WK_FAHRTKOSTEN: _G.WERBUNGSKOSTEN,
    _C.WK_HOMEOFFICE: _G.WERBUNGSKOSTEN,
    _C.WK_ARBEITSZIMMER: _G.WERBUNGSKOSTEN,
    _C.WK_BEWERBUNG: _G.WERBUNGSKOSTEN,
    _C.WK_KONTOFUEHRUNG: _G.WERBUNGSKOSTEN,
    _C.WK_BERUFSVERBAND: _G.WERBUNGSKOSTEN,
    _C.WK_DOPPELTE_HAUSHALTSFUEHRUNG: _G.WERBUNGSKOSTEN,
    _C.STEUERBERATUNG: _G.WERBUNGSKOSTEN,
    _C.VORSORGE_KV_PV: _G.VORSORGE,
    _C.VORSORGE_RV: _G.VORSORGE,
    _C.VORSORGE_RIESTER: _G.VORSORGE,
    _C.VORSORGE_RUERUP: _G.VORSORGE,
    _C.VORSORGE_SONSTIGE: _G.VORSORGE,
    _C.SPENDEN: _G.SONDERAUSGABEN,
    _C.KIRCHENSTEUER: _G.SONDERAUSGABEN,
    _C.KINDERBETREUUNG: _G.KIND,
    _C.SCHULGELD: _G.KIND,
    _C.KRANKHEITSKOSTEN: _G.AGB,
    _C.PFLEGE: _G.AGB,
    _C.BEHINDERUNG: _G.AGB,
    _C.HAUSHALTSNAHE_DIENSTLEISTUNG: _G.HAUSHALTSNAHE,
    _C.HANDWERKERLEISTUNG: _G.HAUSHALTSNAHE,
    _C.V_AFA: _G.VERMIETUNG,
    _C.V_SCHULDZINSEN: _G.VERMIETUNG,
    _C.V_ERHALTUNG: _G.VERMIETUNG,
    _C.V_NEBENKOSTEN: _G.VERMIETUNG,
    _C.KAPITAL_BESCHEINIGUNG: _G.KAPITAL,
    _C.IRRELEVANT: _G.IRRELEVANT,
}

_N = DeductionNote

LABELS_DE: dict[type[StrEnum], dict[StrEnum, str]] = {
    Category: {
        _C.WK_ARBEITSMITTEL: "Arbeitsmittel",
        _C.WK_FORTBILDUNG: "Fortbildung",
        _C.WK_FAHRTKOSTEN: "Fahrtkosten / Entfernungspauschale",
        _C.WK_HOMEOFFICE: "Homeoffice-Pauschale",
        _C.WK_ARBEITSZIMMER: "Arbeitszimmer",
        _C.WK_BEWERBUNG: "Bewerbungskosten",
        _C.WK_KONTOFUEHRUNG: "Kontoführung",
        _C.WK_BERUFSVERBAND: "Gewerkschaft / Berufsverband",
        _C.WK_DOPPELTE_HAUSHALTSFUEHRUNG: "Doppelte Haushaltsführung",
        _C.STEUERBERATUNG: "Steuerberatung",
        _C.VORSORGE_KV_PV: "Kranken- und Pflegeversicherung",
        _C.VORSORGE_RV: "Rentenversicherung",
        _C.VORSORGE_RIESTER: "Riester",
        _C.VORSORGE_RUERUP: "Rürup",
        _C.VORSORGE_SONSTIGE: "Sonstige Versicherungen (Haftpflicht, Unfall, BU)",
        _C.SPENDEN: "Spenden",
        _C.KIRCHENSTEUER: "Kirchensteuer",
        _C.KINDERBETREUUNG: "Kinderbetreuung",
        _C.SCHULGELD: "Schulgeld",
        _C.KRANKHEITSKOSTEN: "Krankheitskosten",
        _C.PFLEGE: "Pflege",
        _C.BEHINDERUNG: "Behinderung",
        _C.HAUSHALTSNAHE_DIENSTLEISTUNG: "Haushaltsnahe Dienstleistung",
        _C.HANDWERKERLEISTUNG: "Handwerkerleistung",
        _C.V_AFA: "AfA (Vermietung)",
        _C.V_SCHULDZINSEN: "Schuldzinsen (Vermietung)",
        _C.V_ERHALTUNG: "Erhaltungsaufwand (Vermietung)",
        _C.V_NEBENKOSTEN: "Nebenkosten (Vermietung)",
        _C.KAPITAL_BESCHEINIGUNG: "Kapitalerträge (Bescheinigung)",
        _C.IRRELEVANT: "Nicht steuerrelevant",
    },
    CategoryGroup: {
        _G.WERBUNGSKOSTEN: "Werbungskosten",
        _G.VORSORGE: "Vorsorgeaufwendungen",
        _G.SONDERAUSGABEN: "Sonderausgaben",
        _G.KIND: "Kinder",
        _G.AGB: "Außergewöhnliche Belastungen",
        _G.HAUSHALTSNAHE: "Haushaltsnahe Aufwendungen (§35a)",
        _G.VERMIETUNG: "Vermietung und Verpachtung",
        _G.KAPITAL: "Kapitalerträge",
        _G.IRRELEVANT: "Nicht steuerrelevant",
    },
    Anlage: {
        Anlage.HAUPTVORDRUCK: "Hauptvordruck",
        Anlage.N: "Anlage N",
        Anlage.VORSORGEAUFWAND: "Anlage Vorsorgeaufwand",
        Anlage.AV: "Anlage AV",
        Anlage.SONDERAUSGABEN: "Anlage Sonderausgaben",
        Anlage.AGB: "Anlage Außergewöhnliche Belastungen",
        Anlage.HAUSHALTSNAHE_AUFWENDUNGEN: "Anlage Haushaltsnahe Aufwendungen",
        Anlage.KIND: "Anlage Kind",
        Anlage.KAP: "Anlage KAP",
        Anlage.V: "Anlage V",
    },
    DocType: {
        DocType.GENERIC_BILL: "Rechnung / Beleg",
        DocType.LOHNSTEUERBESCHEINIGUNG: "Lohnsteuerbescheinigung",
        DocType.JAHRESSTEUERBESCHEINIGUNG: "Jahressteuerbescheinigung",
        DocType.NEBENKOSTENABRECHNUNG: "Nebenkostenabrechnung",
        DocType.KINDERGELD_BESCHEID: "Kindergeldbescheid",
        DocType.ELTERNGELD_BESCHEID: "Elterngeldbescheid",
        DocType.ALG_BESCHEID: "Bescheid Arbeitslosengeld",
        DocType.OTHER: "Sonstiges Dokument",
    },
    DocumentStatus: {
        DocumentStatus.QUEUED: "Wartet",
        DocumentStatus.PROCESSING: "In Bearbeitung",
        DocumentStatus.DONE: "Fertig",
        DocumentStatus.NEEDS_ATTENTION: "Prüfung nötig",
        DocumentStatus.FAILED: "Fehlgeschlagen",
    },
    AttentionReason: {
        AttentionReason.CLASSIFICATION_FAILED: "Belegart konnte nicht erkannt werden",
        AttentionReason.EXTRACTION_FAILED: "Werte konnten nicht ausgelesen werden",
        AttentionReason.UNREADABLE: "Beleg nicht lesbar",
        AttentionReason.MULTIPLE_DOCUMENTS: "Mehrere Belege in einer Datei",
        AttentionReason.DOC_TYPE_NOT_SUPPORTED: "Dokumentart wird noch nicht ausgewertet",
        AttentionReason.POSSIBLE_DUPLICATE: "Möglicherweise doppelt erfasst",
        AttentionReason.FOREIGN_CURRENCY: "Fremdwährung",
        AttentionReason.IMPLAUSIBLE_AMOUNT: "Betrag unplausibel",
        AttentionReason.IMPLAUSIBLE_DATE: "Datum unplausibel",
        AttentionReason.SUM_MISMATCH: "Positionen ergeben nicht den Gesamtbetrag",
        AttentionReason.SIGN_MISMATCH: "Gutschrift und Vorzeichen passen nicht zusammen",
        AttentionReason.CREDIT_NOTE_35A: "Gutschrift zu haushaltsnahen Aufwendungen",
        AttentionReason.LABOUR_SHARE_MISSING: "Arbeitskostenanteil fehlt",
        AttentionReason.PAYMENT_METHOD_UNKNOWN_35A: "Zahlungsart unbekannt (§35a nur unbar)",
        AttentionReason.MULTIPLE_CATEGORIES: "Mehrere Kategorien auf einem Beleg",
        AttentionReason.ASSET_DEPRECIATION: "Abschreibung (AfA) geschätzt, Nutzungsdauer prüfen",
        AttentionReason.DATE_MISSING: "Datum fehlt",
        AttentionReason.UNSUPPORTED_YEAR: "Steuerjahr wird nicht unterstützt",
        AttentionReason.YEAR_BOUNDARY_RECURRING: "Zahlung um den Jahreswechsel (10-Tage-Regel)",
        AttentionReason.LOW_CONFIDENCE: "Unsichere Erkennung",
    },
    FilingStatus: {
        FilingStatus.SINGLE: "Einzelveranlagung (Grundtarif)",
        FilingStatus.JOINT: "Zusammenveranlagung (Splittingtarif)",
    },
    PersonKind: {
        PersonKind.ADULT: "Erwachsene Person",
        PersonKind.CHILD: "Kind",
    },
    Religion: {
        Religion.NONE: "Keine / nicht kirchensteuerpflichtig",
        Religion.EV: "Evangelisch",
        Religion.RK: "Römisch-katholisch",
        Religion.OTHER: (
            "Andere kirchensteuerpflichtige Gemeinschaft (z. B. altkatholisch, jüdisch)"
        ),
    },
    Bundesland: {
        Bundesland.BW: "Baden-Württemberg",
        Bundesland.BY: "Bayern",
        Bundesland.BE: "Berlin",
        Bundesland.BB: "Brandenburg",
        Bundesland.HB: "Bremen",
        Bundesland.HH: "Hamburg",
        Bundesland.HE: "Hessen",
        Bundesland.MV: "Mecklenburg-Vorpommern",
        Bundesland.NI: "Niedersachsen",
        Bundesland.NW: "Nordrhein-Westfalen",
        Bundesland.RP: "Rheinland-Pfalz",
        Bundesland.SL: "Saarland",
        Bundesland.SN: "Sachsen",
        Bundesland.ST: "Sachsen-Anhalt",
        Bundesland.SH: "Schleswig-Holstein",
        Bundesland.TH: "Thüringen",
    },
    Steuerklasse: {
        Steuerklasse.I: "I",
        Steuerklasse.II: "II",
        Steuerklasse.III: "III",
        Steuerklasse.IV: "IV",
        Steuerklasse.V: "V",
        Steuerklasse.VI: "VI",
    },
    AllowanceShare: {
        AllowanceShare.FULL: "Voller Freibetrag (beide Elternteile)",
        AllowanceShare.HALF: "Halber Freibetrag",
    },
    DeductionNote: {
        _N.PERSON_NOT_IN_RETURN: "Beleg einer Person außerhalb der Erklärung nicht berücksichtigt",
        _N.PERSON_UNASSIGNED: "Beleg keiner Person zugeordnet, nicht berücksichtigt",
        _N.NO_EMPLOYMENT: "Person ohne Beschäftigung, Beleg nicht berücksichtigt",
        _N.EXCLUDED_ATTENTION: "Beleg mit auffälligem Betrag nicht berücksichtigt",
        _N.INCLUDED_UNREVIEWED: "Beleg noch ungeprüft, mitgerechnet",
        _N.NET_NEGATIVE_CLIPPED: "Gutschriften übersteigen die Kosten, Summe auf 0 gesetzt",
        _N.COVERED_BY_PAUSCHALE: "Beleg durch die Pauschale abgegolten, nicht addiert",
        _N.ARBEITSZIMMER_REPLACES_HOMEOFFICE: "Arbeitszimmer ersetzt die Homeoffice-Pauschale",
        _N.FAHRTKOSTEN_WITH_ENTFERNUNGSPAUSCHALE: "Fahrtkosten neben Entfernungspauschale prüfen",
        _N.COMMUTE_ASSUMES_CAR: "Arbeitsweg mit eigenem Pkw angenommen",
        _N.DHF_UNCHECKED: "Doppelte Haushaltsführung ohne Prüfung der Höchstgrenzen",
        _N.CHILD_NOT_ELIGIBLE: "Kind nicht berücksichtigt",
        _N.CHILD_AGE_REVIEW: "Alter oder Behinderung des Kindes bitte prüfen",
        _N.CHILD_UNASSIGNED: "Beleg keinem Kind zugeordnet",
        _N.CASH_EXCLUDED: "Barzahlung nicht abziehbar",
        _N.PAYMENT_UNVERIFIED: "Zahlungsweg nicht belegt, bitte prüfen",
        _N.SPENDEN_OVER_LIMIT: "Spenden über der Höchstgrenze, Rest nicht abziehbar",
        _N.LABOUR_SHARE_MISSING: "Lohnanteil fehlt",
        _N.EMPLOYER_SHARE_ASSUMED: "Arbeitgeberanteil RV gleich Arbeitnehmeranteil angenommen",
        _N.KV_PV_UNSPLIT: "Kranken- und Pflegebeitrag nicht getrennt, 4 % Kürzung für beide",
        _N.RIESTER_NOT_SUPPORTED: "Riester-Beitrag noch nicht berücksichtigt",
        _N.CHILD_OVER_25_DISABLED: "Kind über 25 mit Behinderung, Voraussetzungen nicht geprüft",
    },
    PaymentMethod: {
        PaymentMethod.CASH: "Bar",
        PaymentMethod.BANK_TRANSFER: "Überweisung",
        PaymentMethod.DIRECT_DEBIT: "Lastschrift",
        PaymentMethod.CARD: "Karte",
        PaymentMethod.PAYPAL: "PayPal",
        PaymentMethod.OTHER: "Sonstige",
        PaymentMethod.UNKNOWN: "Unbekannt",
    },
}
"""German display labels per enum class. Keyed by class because StrEnum values of
different enums can be equal (e.g. `Anlage.KIND == CategoryGroup.KIND`)."""

ALL_ENUMS: tuple[type[StrEnum], ...] = (
    UserRole,
    PersonKind,
    Religion,
    Channel,
    DocumentStatus,
    DocType,
    ExtractionStep,
    Anlage,
    PaymentMethod,
    FilingStatus,
    DeductionNote,
    AuditAction,
    ActorType,
    CategoryGroup,
    Category,
    JobKind,
    JobStatus,
    AttentionReason,
    Bundesland,
    Steuerklasse,
    AllowanceShare,
)
