"""
Tests for Phase P1 (Intelligence & Benchmarks) of the Agentic Payment
Security Runtime expansion:

- Incident report's deterministic_appendix (Intent Firewall / Deception Lab /
  Kill Chain facts, present regardless of LLM availability)
- Agent Council roster overlap with the master prompt's 5 named roles
  (confirmed via inspection - no new agents were added)
"""

import asyncio

from app.ai.agents import AGENT_CATALOG, write_incident_report
from app.arena.orchestrator import ArenaBattleOrchestrator


class TestIncidentReportAppendix:
    def _run_round(self, round_number: int):
        async def go():
            orch = ArenaBattleOrchestrator()
            result = await orch.run_round_stream(round_number=round_number, dtl_enabled=True, speed=100.0)
            return orch, result
        return asyncio.run(go())

    def test_appendix_present_even_when_llm_is_unavailable(self):
        """No API keys are configured in this environment - status will be
        LLM_UNAVAILABLE, but the FACTS must still be there."""
        orch, result = self._run_round(2)  # CROSS_RAIL_SPLIT
        report = write_incident_report(result, orch.recorder.timeline())
        assert "deterministic_appendix" in report
        assert report["deterministic_appendix"]["kill_chain_stage_code"] == "CROSS_RAIL_SPLIT"

    def test_appendix_reflects_real_kill_chain_score(self):
        orch, result = self._run_round(2)
        report = write_incident_report(result, orch.recorder.timeline())
        appendix = report["deterministic_appendix"]
        assert appendix["attack_chain_score"] == result["kill_chain"]["attack_chain_score"]
        assert appendix["economic_exposure_prevented_inr"] == result["kill_chain"]["economic_exposure_prevented_inr"]

    def test_appendix_counts_intent_firewall_hard_drift(self):
        orch, result = self._run_round(10)  # BENEFICIARY_DRIFT - the diverted leg is HARD_DRIFT
        report = write_incident_report(result, orch.recorder.timeline())
        appendix = report["deterministic_appendix"]
        assert appendix["intent_firewall_hard_drift_count"] == 1
        assert "beneficiary_drift" in appendix["intent_firewall_violating_dimensions"]

    def test_appendix_counts_deception_lab_detections(self):
        orch, result = self._run_round(13)  # CONTEXT_MEMORY_POISONING
        report = write_incident_report(result, orch.recorder.timeline())
        appendix = report["deterministic_appendix"]
        assert appendix["deception_lab_detection_count"] == 1
        assert "CONTEXT_MEMORY_POISONING" in appendix["deception_lab_types"]

    def test_clean_round_reports_zero_detections_not_missing_keys(self):
        """A round with no drift/deception must still have well-shaped zeros, not absent keys."""
        orch, result = self._run_round(11)  # PROMPT_INJECTION - flags deception but not firewall
        report = write_incident_report(result, orch.recorder.timeline())
        appendix = report["deterministic_appendix"]
        assert appendix["intent_firewall_hard_drift_count"] == 0
        assert appendix["intent_firewall_violating_dimensions"] == []


class TestAgentCouncilOverlap:
    """
    The master prompt names 5 Agent Council roles. Confirms (rather than
    assumes) that all 5 already exist in the 12-agent roster, so Phase P1
    correctly added nothing here instead of duplicating existing agents.
    """

    def test_all_five_named_roles_already_exist(self):
        ids = {a["id"] for a in AGENT_CATALOG}
        for expected in ("intent_compiler", "cart_auditor", "red_strategist",
                          "counterfactual_analyst", "incident_report"):
            assert expected in ids

    def test_roster_is_still_exactly_twelve(self):
        assert len(AGENT_CATALOG) == 12
