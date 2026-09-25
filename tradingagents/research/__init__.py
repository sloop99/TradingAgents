"""Free-first, source-preserving equity research foundation."""

from .checks import (
    CheckResult,
    canonical_metric,
    classify_business,
    derive_free_cash_flow,
    run_checks,
)
from .engine import ResearchProvider, build_packet, parse_as_of
from .models import (
    BusinessModel,
    CoverageStatus,
    Document,
    EvidenceDocument,
    EvidenceFact,
    Fact,
    FactKind,
    Issue,
    IssuerIdentity,
    IssueSeverity,
    ResearchIssue,
    ResearchPacket,
)

__all__ = [
    "BusinessModel",
    "CheckResult",
    "CoverageStatus",
    "Document",
    "EvidenceDocument",
    "EvidenceFact",
    "Fact",
    "FactKind",
    "Issue",
    "IssueSeverity",
    "IssuerIdentity",
    "ResearchIssue",
    "ResearchPacket",
    "ResearchProvider",
    "build_packet",
    "canonical_metric",
    "classify_business",
    "derive_free_cash_flow",
    "parse_as_of",
    "run_checks",
]
