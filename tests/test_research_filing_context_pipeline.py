from tradingagents.research import ResearchPacket, build_packet
from tradingagents.research.filings import extract_filing_evidence


def payload():
    source = '''<html xmlns="http://www.w3.org/1999/xhtml"
      xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
      xmlns:xbrli="http://www.xbrl.org/2003/instance"
      xmlns:iso4217="http://www.xbrl.org/2003/iso4217"
      xmlns:us-gaap="http://fasb.org/us-gaap/2026">
      <body><ix:resources>
      <xbrli:unit id="usd"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>
      <xbrli:context id="balance"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">0000009999</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:instant>2026-06-30</xbrli:instant></xbrli:period></xbrli:context>
      </ix:resources>
      <ix:nonFraction name="us-gaap:LongTermDebtCurrent" contextRef="balance" unitRef="usd" id="current">0</ix:nonFraction>
      <ix:nonFraction name="us-gaap:LongTermDebtNoncurrent" contextRef="balance" unitRef="usd" id="noncurrent">500</ix:nonFraction>
      </body></html>'''
    result = extract_filing_evidence(source, ticker="WIDE", cik="0000009999",
                                    accession="0000009999-26-000001",
                                    source_url="https://www.sec.gov/Archives/edgar/data/9999/000000999926000001/report.htm",
                                    published_at="2026-08-01T12:00:00Z", retrieved_at="2026-09-16T12:00:00Z")
    result["metadata"] = {"filings": [result["metadata"]]}
    return result


class Provider:
    def __init__(self, data):
        self.data = data
    def fetch(self, ticker, as_of):
        return self.data


def test_arbitrary_issuer_scoped_subtotal_roundtrip_and_no_total_debt_promotion():
    packet = build_packet("WIDE", "2026-09-16", [Provider(payload())])
    totals = [f for f in packet.facts if f.metric == "filing_long_term_debt_subtotal"]
    assert len(totals) == 1 and totals[0].value == 500
    assert len(totals[0].input_fact_ids) == 2
    assert not any(f.metric in {"total_debt", "enterprise_value", "current_shares"} for f in packet.facts)
    assert packet.financial_analysis["filing_reconciliation"]["status"] == "partial"
    assert "Scoped reconciliation" in packet.to_markdown()
    assert "Inline filing candidates: 2" in packet.render_context()
    assert ResearchPacket.from_dict(packet.to_dict()).to_dict() == packet.to_dict()


def test_future_or_wrong_issuer_metadata_cannot_restore_filtered_facts():
    source = payload()
    future = build_packet("WIDE", "2026-07-31", [Provider(source)])
    assert not any(f.metric == "filing_long_term_debt_subtotal" for f in future.facts)
    wrong = build_packet("OTHER", "2026-09-16", [Provider(source)])
    assert wrong.facts == []
    assert wrong.financial_analysis["filing_reconciliation"]["status"] == "unsupported"
