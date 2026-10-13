"""All domain models. Importing this package registers every table on `Base.metadata`."""

from app.db.models.app_user import AppUser
from app.db.models.audit_log import AuditLog
from app.db.models.auth import MagicLinkToken, UserSession
from app.db.models.channel import ChannelLink, ChannelLinkCode
from app.db.models.document import Document
from app.db.models.estimate import Estimate
from app.db.models.extraction import Extraction
from app.db.models.household import Household
from app.db.models.job import Job
from app.db.models.passkey import Passkey
from app.db.models.person import Person
from app.db.models.profile import ChildYear, Employment, Lohnersatz, TaxProfile
from app.db.models.tax_item import TaxItem

__all__ = [
    "AppUser",
    "AuditLog",
    "ChannelLink",
    "ChannelLinkCode",
    "ChildYear",
    "Document",
    "Employment",
    "Estimate",
    "Extraction",
    "Household",
    "Job",
    "Lohnersatz",
    "MagicLinkToken",
    "Passkey",
    "Person",
    "TaxItem",
    "TaxProfile",
    "UserSession",
]
