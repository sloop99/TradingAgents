from tradingagents.research import build_packet


def test_packet_context_includes_horizon_and_thesis():
    packet = build_packet(
        "TEST",
        "2026-09-16",
        [],
        horizon="five_years",
        thesis="Security platform compounds recurring revenue",
    )

    context = packet.render_context()

    assert "Horizon: five_years" in context
    assert "User thesis (unverified): Security platform compounds recurring revenue" in context
