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
    """Church-tax relevant denomination; #13 may refine this."""

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
    AuditAction,
    ActorType,
    CategoryGroup,
    Category,
)
