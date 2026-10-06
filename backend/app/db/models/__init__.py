"""All domain models. Importing this package registers every table on `Base.metadata`."""

from app.db.models.app_user import AppUser
from app.db.models.audit_log import AuditLog
from app.db.models.auth import MagicLinkToken, UserSession
from app.db.models.document import Document
from app.db.models.extraction import Extraction
from app.db.models.household import Household
from app.db.models.person import Person
from app.db.models.tax_item import TaxItem

__all__ = [
    "AppUser",
    "AuditLog",
    "Document",
    "Extraction",
    "Household",
    "MagicLinkToken",
    "Person",
    "TaxItem",
    "UserSession",
]
