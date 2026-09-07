"""
Tests for BrainDecisionTrace: Step recording, audit checkpoints, and veto requirement tracking.
"""

from src.bybit.telemetry.brain_trace import BrainDecisionTrace


def test_brain_decision_trace_candidate_and_veto():
    trace = BrainDecisionTrace(max_history=10)

    # 1. Candidate found
    trace.record_candidate_found("AVAXUSDT", "TURTLE_SOUP", 84.5)

    # 2. SMC Audit
    trace.record_smc_audit("AVAXUSDT", {"Structure": "PASS", "Liquidity": "PASS"}, passed=True)

    # 3. Veto with comparison
    trace.record_veto("TRUMPUSDT", "RR_TP2", requirement="2.80", actual="2.41")

    # 4. Trade approved
    trace.record_trade_approved("AVAXUSDT", "Buy", 10.5, 28.50, 27.80)

    recent = trace.get_recent(limit=5)
    assert len(recent) == 4

    # Latest should be trade approved (returned in reversed chronological order)
    assert recent[0]["stage"] == "POLICY"
    assert recent[0]["status"] == "PASS"

    # Veto check
    veto_step = [s for s in recent if s["stage"] == "VETO"][0]
    assert veto_step["requirement"] == "2.80"
    assert veto_step["actual"] == "2.41"
    assert "2.41 vs required 2.80" in veto_step["message"]


def test_brain_decision_trace_bounded_history():
    trace = BrainDecisionTrace(max_history=5)
    for i in range(12):
        trace.record_step("SOLUSDT", "SCAN", "INFO", f"Step {i}")

    recent = trace.get_recent(limit=10)
    assert len(recent) == 5
    assert "Step 11" in recent[0]["message"]
