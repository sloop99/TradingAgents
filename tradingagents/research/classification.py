"""Conservative, generic business-model classification from issuer identity.

The ranges and labels follow the SEC's SIC list
(https://www.sec.gov/search-filings/standard-industrial-classification-sic-code-list)
and OSHA's SIC manual for computer services/software
(https://www.osha.gov/sic-manual/7372).  SIC is a useful disclosure category,
not proof of revenue mix; this module therefore withholds specialized checks
where a code cannot distinguish hardware from software or subscriptions.
"""

from __future__ import annotations

from .models import BusinessModel, IssuerIdentity, IssueSeverity, ResearchIssue

_MODEL_VALUES = {model.value for model in BusinessModel}


def _issue(code: str, message: str, severity: IssueSeverity) -> ResearchIssue:
    return ResearchIssue(code=code, message=message, severity=severity)


def _supplied_model(identity: IssuerIdentity | None) -> BusinessModel | None:
    if identity is None or not identity.business_model:
        return None
    value = identity.business_model.strip().casefold()
    return BusinessModel(value) if value in _MODEL_VALUES else None


def _sic_model(sic: int) -> tuple[BusinessModel, list[ResearchIssue]]:
    """Return the SIC result and any caveat required by that result.

    SEC lists 357x as computer equipment and 366x as communications equipment;
    those categories can contain meaningful software/subscription businesses,
    so they remain GENERAL pending issuer-specific evidence.
    """
    if 6000 <= sic <= 6099:
        return BusinessModel.BANK, []
    if 6100 <= sic <= 6199:
        return BusinessModel.GENERAL, [
            _issue(
                "BUSINESS_CLASSIFICATION_NONBANK_FINANCE",
                f"SIC {sic} is in the 61xx credit-institution range, but is not a 60xx bank; bank checks were withheld.",
                IssueSeverity.WARNING,
            )
        ]
    if sic == 6798:
        return BusinessModel.REIT, []
    if 5200 <= sic <= 5999:
        return BusinessModel.RETAIL, []
    if sic == 7372:
        return BusinessModel.SOFTWARE, []
    if 7370 <= sic <= 7379:
        return BusinessModel.GENERAL, [
            _issue(
                "BUSINESS_CLASSIFICATION_PROVISIONAL",
                f"SIC {sic} is in broad computer-related services (737x); software checks were withheld pending more specific evidence.",
                IssueSeverity.WARNING,
            )
        ]
    if 3570 <= sic <= 3579 or 3660 <= sic <= 3669:
        return BusinessModel.GENERAL, [
            _issue(
                "BUSINESS_CLASSIFICATION_HARDWARE_MIX",
                f"SIC {sic} identifies computer or communications equipment, but cannot establish the issuer's software/subscription mix; specialized methods were withheld.",
                IssueSeverity.WARNING,
            )
        ]
    if 2000 <= sic <= 3999:
        return BusinessModel.INDUSTRIAL, [
            _issue(
                "BUSINESS_CLASSIFICATION_COARSE",
                f"SIC {sic} is in broad manufacturing (20xx–39xx); industrial checks are a coarse classification.",
                IssueSeverity.INFO,
            )
        ]
    return BusinessModel.GENERAL, [
        _issue(
            "BUSINESS_CLASSIFICATION_GENERAL",
            f"SIC {sic} does not map to a Phase 1 specialized model; general-company checks were used.",
            IssueSeverity.INFO,
        )
    ]


def classify_business(identity: IssuerIdentity | None) -> tuple[BusinessModel, list[ResearchIssue]]:
    """Classify an issuer without using its ticker or name.

    A numeric SIC takes precedence over provider metadata.  A provider-supplied
    ``business_model`` is accepted only as a provisional fallback when SIC is
    absent or malformed; if it conflicts with a valid SIC, the conflict is
    surfaced rather than silently trusting the provider.
    """
    supplied = _supplied_model(identity)
    raw_sic = identity.sic.strip() if identity and identity.sic else None
    if not raw_sic:
        if supplied is not None:
            return supplied, [
                _issue(
                    "BUSINESS_CLASSIFICATION_LIMITED",
                    f"No SIC was available; the provider-supplied {supplied.value!r} business model is provisional.",
                    IssueSeverity.WARNING,
                )
            ]
        return BusinessModel.GENERAL, [
            _issue(
                "BUSINESS_CLASSIFICATION_LIMITED",
                "No SIC was available; general-company checks are a conservative fallback.",
                IssueSeverity.WARNING,
            )
        ]

    try:
        sic = int(raw_sic)
    except ValueError:
        if supplied is not None:
            return supplied, [
                _issue(
                    "BUSINESS_CLASSIFICATION_LIMITED",
                    f"SIC {raw_sic!r} is not numeric; the provider-supplied {supplied.value!r} business model is provisional.",
                    IssueSeverity.WARNING,
                )
            ]
        return BusinessModel.GENERAL, [
            _issue(
                "BUSINESS_CLASSIFICATION_LIMITED",
                f"SIC {raw_sic!r} is not numeric; general-company checks were used.",
                IssueSeverity.WARNING,
            )
        ]

    model, issues = _sic_model(sic)
    if supplied is not None and supplied is not model:
        issues.append(
            _issue(
                "BUSINESS_CLASSIFICATION_CONFLICT",
                f"Provider-supplied business model {supplied.value!r} conflicts with SIC {sic} classification {model.value!r}; SIC classification was used.",
                IssueSeverity.WARNING,
            )
        )
    return model, issues
