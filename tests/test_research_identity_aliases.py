import pytest

from tradingagents.research.engine import _merge_identities
from tradingagents.research.models import IssuerIdentity


@pytest.mark.parametrize("alias", ["NasdaqGS", "NasdaqGM", "NasdaqCM", "NMS", "NGM", "NCM"])
def test_named_nasdaq_aliases_merge_without_identity_conflict(alias):
    merged, issues = _merge_identities(
        "FTNT",
        [
            IssuerIdentity(ticker="FTNT", exchange="Nasdaq"),
            IssuerIdentity(ticker="FTNT", exchange=alias),
        ],
    )

    assert merged is not None
    assert merged.exchange == "Nasdaq"
    assert not any(issue.code == "IDENTITY_CONFLICT" for issue in issues)


def test_nyse_and_nyq_merge_without_identity_conflict():
    merged, issues = _merge_identities(
        "PANW",
        [
            IssuerIdentity(ticker="PANW", exchange="NYSE"),
            IssuerIdentity(ticker="PANW", exchange="NYQ"),
        ],
    )

    assert merged is not None
    assert merged.exchange == "NYSE"
    assert not any(issue.code == "IDENTITY_CONFLICT" for issue in issues)


def test_different_venues_remain_an_identity_conflict():
    merged, issues = _merge_identities(
        "PANW",
        [
            IssuerIdentity(ticker="PANW", exchange="NasdaqGS"),
            IssuerIdentity(ticker="PANW", exchange="NYSE"),
        ],
    )

    assert merged is not None
    assert merged.exchange is None
    assert any(issue.code == "IDENTITY_CONFLICT" and issue.severity.value == "error" for issue in issues)
