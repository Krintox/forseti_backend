"""
Tests for Module 3 of the Agentic Payment Security Runtime expansion:
the Agentic Payment Kill Chain (lifecycle stage mapping + scoring).
"""

import asyncio

import pytest

from app.arena.orchestrator import ArenaBattleOrchestrator
from app.kill_chain import KILL_CHAIN_STAGES, STRATEGY_TO_STAGE, coverage, score_round, stage_for_strategy
from app.taxonomy import IMPLEMENTED


class TestStageMapping:
    def test_every_implemented_vector_has_a_stage(self):
        """
        Guards against the exact Module-1/2 gap: a vector runnable via the
        orchestrator but silently missing from a downstream lookup table.
        """
        implemented_keys = {row["key"] for row in IMPLEMENTED.values()}
        for key in implemented_keys:
            assert key in STRATEGY_TO_STAGE, f"{key} is implemented but has no kill-chain stage mapping"

    def test_every_mapped_stage_code_is_a_real_stage(self):
        valid_codes = {s["code"] for s in KILL_CHAIN_STAGES}
        for strategy, code in STRATEGY_TO_STAGE.items():
            assert code in valid_codes, f"{strategy} maps to unknown stage code {code}"

    def test_eleven_stages_defined_in_order(self):
        assert len(KILL_CHAIN_STAGES) == 11
        assert [s["index"] for s in KILL_CHAIN_STAGES] == list(range(1, 12))

    def test_unmapped_strategy_returns_none(self):
        assert stage_for_strategy("NOT_A_REAL_STRATEGY") is None

    def test_known_strategy_returns_its_stage(self):
        stage = stage_for_strategy("CROSS_RAIL_SPLIT")
        assert stage is not None
        assert stage["code"] == "CROSS_RAIL_SPLIT"


class TestScoreRound:
    def _run(self, round_number: int) -> dict:
        async def go():
            orch = ArenaBattleOrchestrator()
            return await orch.run_round_stream(round_number=round_number, dtl_enabled=True, speed=100.0)
        return asyncio.run(go())

    def test_contained_round_scores_a_positive_chain_score(self):
        result = self._run(2)  # CROSS_RAIL_SPLIT, flagship, always contained
        kc = result["kill_chain"]
        assert kc["stage"]["code"] == "CROSS_RAIL_SPLIT"
        assert kc["contained"] is True
        assert kc["attack_chain_score"] > 0.0
        assert kc["time_to_detection_ms"] is not None
        assert kc["time_to_detection_ms"] >= 0

    def test_economic_exposure_prevented_matches_the_violation_overshoot(self):
        result = self._run(2)
        kc = result["kill_chain"]
        violation_events = [e for e in result["events"] if e["event_type"] == "INVARIANT_VIOLATION"]
        expected = sum(e["payload"]["overshoot"] for e in violation_events)
        assert kc["economic_exposure_prevented_inr"] == pytest.approx(expected)

    def test_blast_radius_reflects_distinct_rails_touched(self):
        result = self._run(2)  # cross-rail split touches multiple rails
        kc = result["kill_chain"]
        assert len(kc["rails_touched"]) >= 2
        assert kc["blast_radius_score"] == pytest.approx(len(kc["rails_touched"]) / 3.0)

    def test_second_round_is_not_confused_by_the_first_rounds_events(self):
        """
        Regression test for the accumulated-recorder-timeline bug: running
        two rounds back to back on the SAME orchestrator (no reset in
        between) must not let round 2's score_round pick up round 1's
        ATTACK_STARTED offset.
        """
        async def go():
            orch = ArenaBattleOrchestrator()
            r1 = await orch.run_round_stream(round_number=2, dtl_enabled=True, speed=100.0)
            r2 = await orch.run_round_stream(round_number=9, dtl_enabled=True, speed=100.0)
            return orch, r1, r2
        orch, r1, r2 = asyncio.run(go())

        assert len(orch.recorder.events) > len(r2["step_results"]) * 6  # recorder really did accumulate
        assert r2["kill_chain"]["stage"]["code"] == "DELEGATION_ABUSE"  # LAPSED_MANDATE's stage
        # round 2's own detection latency, not inflated by round 1's history
        assert r2["kill_chain"]["time_to_detection_ms"] < 5000


class TestCoverage:
    def test_empty_history_reports_zero_coverage(self):
        result = coverage([])
        assert result["stages_reached"] == 0
        assert result["coverage_pct"] == 0.0

    def test_coverage_accumulates_across_rounds(self):
        scores = [
            score_round({"strategy": "CROSS_RAIL_SPLIT", "winner": "BLUE", "detected": True,
                         "events": [], "step_results": [], "round_number": 1}),
            score_round({"strategy": "PROMPT_INJECTION", "winner": "NONE", "detected": False,
                         "events": [], "step_results": [], "round_number": 2}),
        ]
        result = coverage(scores)
        assert result["stages_reached"] == 2
        assert result["stages_contained"] == 1  # only CROSS_RAIL_SPLIT was contained
        assert result["unmapped_rounds"] == 0

    def test_unmapped_strategy_counted_separately(self):
        scores = [score_round({"strategy": "NOT_A_REAL_STRATEGY", "winner": "NONE", "detected": False,
                               "events": [], "step_results": [], "round_number": 1})]
        result = coverage(scores)
        assert result["stages_reached"] == 0
        assert result["unmapped_rounds"] == 1

    def test_session_coverage_survives_a_full_9_round_campaign(self):
        """The original 9 vectors (rounds 1-9) span 6 distinct stages."""
        async def go():
            orch = ArenaBattleOrchestrator()
            for rn in range(1, 10):
                await orch.run_round_stream(round_number=rn, dtl_enabled=True, speed=100.0)
            return orch
        orch = asyncio.run(go())
        result = coverage(orch.round_history)
        assert result["unmapped_rounds"] == 0
        assert result["stages_reached"] >= 4
