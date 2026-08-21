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
        assert risk["risk_components"]["dtl_invariant_risk"] == 1.0

    def test_intent_firewall_component_uses_max_drift(self):
        round_result = {
            "firewall_verdicts": [
                {"overall_drift_score": 0.2},
                {"overall_drift_score": 0.82},
                {"overall_drift_score": 0.0},
            ],
        }
        risk = compute_unified_risk(round_result)
        assert risk["risk_components"]["intent_firewall_risk"] == 0.82

    def test_deception_component_is_binary(self):
        clean = compute_unified_risk({"deception_verdicts": [{"verdict": "CLEAN"}]})
        detected = compute_unified_risk({"deception_verdicts": [{"verdict": "DECEPTION_DETECTED"}]})
        assert clean["risk_components"]["deception_lab_risk"] == 0.0
        assert detected["risk_components"]["deception_lab_risk"] == 1.0

    def test_ml_component_uses_max_probability(self):
        round_result = {"step_results": [{"ml_probability": 0.1}, {"ml_probability": 0.73}]}
        risk = compute_unified_risk(round_result)
        assert risk["risk_components"]["ml_anomaly_risk"] == 0.73

    def test_kill_chain_component_passthrough(self):
        risk = compute_unified_risk({"kill_chain": {"attack_chain_score": 0.65}})
        assert risk["risk_components"]["kill_chain_risk"] == 0.65

    def test_overall_is_equal_weighted_mean(self):
        round_result = {
            "detected": True,  # 1.0
            "firewall_verdicts": [{"overall_drift_score": 0.5}],  # 0.5
            "deception_verdicts": [{"verdict": "CLEAN"}],  # 0.0
            "step_results": [{"ml_probability": 0.4}],  # 0.4
            "kill_chain": {"attack_chain_score": 0.6},  # 0.6
        }
        risk = compute_unified_risk(round_result)
        expected = (1.0 + 0.5 + 0.0 + 0.4 + 0.6) / 5
        assert risk["overall_risk_score"] == round(expected, 4)


class TestOrchestratorIntegration:
    def test_risk_attached_to_every_round(self):
        async def go():
            orch = ArenaBattleOrchestrator()
            return await orch.run_round_stream(round_number=2, dtl_enabled=True, speed=100.0)

        result = asyncio.run(go())
        assert "risk" in result
        assert result["risk"]["deterministic_override"] == result["detected"]
