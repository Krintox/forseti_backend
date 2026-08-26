"""
Tests for the Unified Risk Engine (Phase P2): a composite synthesis of
signals other modules already computed for a round, not a new detector.
"""

import asyncio

from app.arena.orchestrator import ArenaBattleOrchestrator
from app.risk_engine import compute_unified_risk


class TestComputeUnifiedRisk:
    def test_empty_round_scores_zero(self):
        risk = compute_unified_risk({})
        assert risk["overall_risk_score"] == 0.0
        assert risk["deterministic_override"] is False

    def test_detected_round_sets_deterministic_override(self):
        risk = compute_unified_risk({"detected": True})
        assert risk["deterministic_override"] is True

    def test_authority_breach_severity_is_a_magnitude_not_a_boolean(self):
        """
        The component formerly named `dtl_invariant_risk` was `1.0 if detected
        else 0.0` - a restatement of the outcome, and one of THREE components
        that traced back to that single boolean. It is now the SHARE of the
        attempted objective that was actually stopped.
        """
        partial = compute_unified_risk({
            "detected": True,
            "step_results": [{"tx": {"amount": 10000.0}}],
            "kill_chain": {"economic_exposure_prevented_inr": 2000.0},
        })
        total = compute_unified_risk({
            "detected": True,
            "step_results": [{"tx": {"amount": 10000.0}}],
            "kill_chain": {"economic_exposure_prevented_inr": 10000.0},
        })
        assert partial["risk_components"]["authority_breach_severity"] == 0.2
        assert total["risk_components"]["authority_breach_severity"] == 1.0

    def test_intent_drift_component_uses_max_drift(self):
        round_result = {
            "firewall_verdicts": [
                {"overall_drift_score": 0.2},
                {"overall_drift_score": 0.82},
                {"overall_drift_score": 0.0},
            ],
        }
        risk = compute_unified_risk(round_result)
        assert risk["risk_components"]["intent_drift_severity"] == 0.82

    def test_components_are_mutually_independent(self):
        """
        The core fix: no component may be derivable from another. Setting ONE
        input must move exactly ONE component.
        """
        base = compute_unified_risk({})["risk_components"]
        assert set(base) == {
            "authority_breach_severity", "intent_drift_severity",
            "deception_lab_risk", "ml_anomaly_risk", "structural_integrity_risk",
        }

        moved = {}
        probes = {
            "authority_breach_severity": {
                "step_results": [{"tx": {"amount": 1000.0}}],
                "kill_chain": {"economic_exposure_prevented_inr": 500.0}},
            "intent_drift_severity": {"firewall_verdicts": [{"overall_drift_score": 0.7}]},
            "deception_lab_risk": {"deception_verdicts": [{"verdict": "DECEPTION_DETECTED"}]},
            "ml_anomaly_risk": {"step_results": [{"ml_probability": 0.6}]},
            "structural_integrity_risk": {"settlement_verdict": {"verdict": "CONFLICT_DETECTED"}},
        }
        for target, payload in probes.items():
            comps = compute_unified_risk(payload)["risk_components"]
            changed = {k for k, v in comps.items() if v != base[k]}
            moved[target] = changed
            assert changed <= {target}, (
                f"setting only {target}'s input also moved {changed - {target}} - "
                f"the components are not independent"
            )
        assert all(moved[t] == {t} for t in probes), f"a probe moved nothing: {moved}"

    def test_deception_component_is_binary(self):
        clean = compute_unified_risk({"deception_verdicts": [{"verdict": "CLEAN"}]})
        detected = compute_unified_risk({"deception_verdicts": [{"verdict": "DECEPTION_DETECTED"}]})
        assert clean["risk_components"]["deception_lab_risk"] == 0.0
        assert detected["risk_components"]["deception_lab_risk"] == 1.0

    def test_ml_component_uses_max_probability(self):
        round_result = {"step_results": [{"ml_probability": 0.1}, {"ml_probability": 0.73}]}
        risk = compute_unified_risk(round_result)
        assert risk["risk_components"]["ml_anomaly_risk"] == 0.73

    def test_structural_integrity_component_covers_settlement_and_chain(self):
        """
        Post-authorization and delegation-structure failures are not implied by
        any authority-dimension outcome, which is why they are their own term.
        """
        settlement = compute_unified_risk(
            {"settlement_verdict": {"verdict": "CONFLICT_DETECTED"}})
        chain = compute_unified_risk(
            {"chain_violations": [{"code": "CHAIN_ATTESTATION_INVALID"}]})
        clean = compute_unified_risk({"settlement_verdict": {"verdict": "CONSISTENT"}})
        assert settlement["risk_components"]["structural_integrity_risk"] == 1.0
        assert chain["risk_components"]["structural_integrity_risk"] == 0.85
        assert clean["risk_components"]["structural_integrity_risk"] == 0.0

    def test_overall_is_equal_weighted_mean(self):
        round_result = {
            "detected": True,
            "step_results": [{"tx": {"amount": 1000.0}, "ml_probability": 0.4}],
            "kill_chain": {"economic_exposure_prevented_inr": 800.0},   # 0.8
            "firewall_verdicts": [{"overall_drift_score": 0.5}],        # 0.5
            "deception_verdicts": [{"verdict": "CLEAN"}],               # 0.0
            "settlement_verdict": {"verdict": "CONSISTENT"},            # 0.0
        }
        risk = compute_unified_risk(round_result)
        expected = (0.8 + 0.5 + 0.0 + 0.4 + 0.0) / 5
        assert risk["overall_risk_score"] == round(expected, 4)


class TestOrchestratorIntegration:
    def test_risk_attached_to_every_round(self):
        async def go():
            orch = ArenaBattleOrchestrator()
            return await orch.run_round_stream(round_number=2, dtl_enabled=True, speed=100.0)

        result = asyncio.run(go())
        assert "risk" in result
        assert result["risk"]["deterministic_override"] == result["detected"]
