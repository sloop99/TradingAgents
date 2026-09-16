from tradingagents.research.classification import classify_business
from tradingagents.research.models import BusinessModel, IssuerIdentity, IssueSeverity


def identity(*, sic: str | None = None, business_model: str | None = None, name: str | None = None) -> IssuerIdentity:
    return IssuerIdentity(ticker="ZZZZ", sic=sic, business_model=business_model, name=name)


def test_hardware_and_communications_equipment_are_general_with_explicit_warning():
    for sic in ("3577", "3663"):
        model, issues = classify_business(identity(sic=sic, name="Any issuer"))
        assert model is BusinessModel.GENERAL
        assert any(issue.code == "BUSINESS_CLASSIFICATION_HARDWARE_MIX" for issue in issues)
        assert any(issue.severity is IssueSeverity.WARNING for issue in issues)


def test_manufacturing_is_coarse_industrial_without_ticker_or_name_logic():
    model, issues = classify_business(identity(sic="3559", name="Completely unrelated name"))
    assert model is BusinessModel.INDUSTRIAL
    assert any(issue.code == "BUSINESS_CLASSIFICATION_COARSE" for issue in issues)


def test_software_7372_and_other_737x_are_distinguished():
    model, issues = classify_business(identity(sic="7372"))
    assert model is BusinessModel.SOFTWARE
    assert not issues

    model, issues = classify_business(identity(sic="7379"))
    assert model is BusinessModel.GENERAL
    assert any(issue.code == "BUSINESS_CLASSIFICATION_PROVISIONAL" for issue in issues)


def test_banks_are_60xx_but_61xx_is_general_nonbank_finance():
    model, issues = classify_business(identity(sic="6021"))
    assert model is BusinessModel.BANK
    assert not issues

    model, issues = classify_business(identity(sic="6159"))
    assert model is BusinessModel.GENERAL
    assert any(issue.code == "BUSINESS_CLASSIFICATION_NONBANK_FINANCE" for issue in issues)


def test_reit_and_retail_ranges():
    assert classify_business(identity(sic="6798"))[0] is BusinessModel.REIT
    assert classify_business(identity(sic="5812"))[0] is BusinessModel.RETAIL


def test_missing_or_unknown_sic_uses_provider_model_only_provisionally():
    model, issues = classify_business(identity(business_model="software"))
    assert model is BusinessModel.SOFTWARE
    assert issues[0].code == "BUSINESS_CLASSIFICATION_LIMITED"
    assert issues[0].severity is IssueSeverity.WARNING

    model, issues = classify_business(identity(sic="not-a-sic", business_model="bank"))
    assert model is BusinessModel.BANK
    assert issues[0].code == "BUSINESS_CLASSIFICATION_LIMITED"

    model, issues = classify_business(identity())
    assert model is BusinessModel.GENERAL
    assert issues[0].code == "BUSINESS_CLASSIFICATION_LIMITED"


def test_provider_conflict_is_reported_and_sic_wins():
    model, issues = classify_business(identity(sic="3577", business_model="software"))
    assert model is BusinessModel.GENERAL
    assert any(issue.code == "BUSINESS_CLASSIFICATION_CONFLICT" for issue in issues)


def test_ticker_and_name_are_not_classification_inputs():
    first = classify_business(identity(sic="3999", name="Software Cloud Inc."))
    second = classify_business(identity(sic="3999", name="Steel Works", business_model="retail"))
    assert first[0] is second[0] is BusinessModel.INDUSTRIAL
