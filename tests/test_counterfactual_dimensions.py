"""
Tests for the Phase P1 counterfactual-engine extension: RAIL and PURPOSE
dimensions alongside the original AMOUNT-only ceiling sweep.

The LLM boundary (`agents.propose_counterfactual`) is monkeypatched with a
canned proposal rather than calling a real provider - no API keys are
configured in this environment, and the code actually worth testing here is
the DETERMINISTIC replay logic downstream of that call (sandbox mutation,
dimension gating, result formatting), which is exactly the boundary the
project's own "LLM proposes, deterministic system verifies" design draws.
"""

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.ai import agents as A
from app.ai.routes import _counterfactual_answer
from app.arena.orchestrator import STRATEGY_AUTHORITY_PROFILE
from app.main import app, orchestrator


class TestDimensionGating:
    def test_fixed_profile_strategies_only_offer_amount(self):
        for strategy in ("RAIL_SCOPE_VIOLATION", "PER_TX_BREACH", "LAPSED_MANDATE",
                          "BENEFICIARY_DRIFT", "CONSTRAINT_EROSION"):
            assert strategy in STRATEGY_AUTHORITY_PROFILE

    def test_free_strategies_have_no_fixed_profile(self):
        for strategy in ("CROSS_RAIL_SPLIT", "INTENT_LAUNDERING", "SCOPE_CREEP",
                          "PROMPT_INJECTION", "TOOL_OUTPUT_POISONING"):
            assert strategy not in STRATEGY_AUTHORITY_PROFILE


class TestCounterfactualAnswerFormatting:
    def test_empty_runs(self):
        assert _counterfactual_answer([]) == "No scenarios were simulated."

    def test_amount_only_reports_best_contained_ceiling(self):
        runs = [
            {"dimension": "AMOUNT", "ceiling_inr": 5000.0, "contained": True, "label": "low"},
            {"dimension": "AMOUNT", "ceiling_inr": 8000.0, "contained": True, "label": "mid"},
            {"dimension": "AMOUNT", "ceiling_inr": 12000.0, "contained": False, "label": "high"},
        ]
        answer = _counterfactual_answer(runs)
        assert "2 of 3" in answer
        assert "8,000" in answer

    def test_amount_none_contained(self):
        runs = [{"dimension": "AMOUNT", "ceiling_inr": 20000.0, "contained": False, "label": "high"}]
        answer = _counterfactual_answer(runs)
        assert "None of the 1" in answer

    def test_rail_and_purpose_runs_report_per_scenario(self):
        runs = [
            {"dimension": "RAIL", "parameter_summary": "CARD_TOKEN disabled", "contained": True},
            {"dimension": "PURPOSE", "parameter_summary": "Gift cards / stored value permitted", "contained": False},
        ]
        answer = _counterfactual_answer(runs)
        assert "CARD_TOKEN disabled: still contained the attack." in answer
        assert "Gift cards / stored value permitted: did NOT contain the attack." in answer


class TestCounterfactualEndToEnd:
    """Monkeypatches the LLM boundary; exercises the real replay logic."""

    def _fake_proposal(self, dims):
        params = []
        if "AMOUNT" in dims:
            params.append({"dimension": "AMOUNT", "ceiling_inr": 5000.0, "label": "half"})
        if "RAIL" in dims:
            params.append({"dimension": "RAIL", "disable_rail": "CARD_TOKEN", "label": "no card"})
        if "PURPOSE" in dims:
            params.append({"dimension": "PURPOSE", "permit_gift_cards": True, "label": "gift cards ok"})
        return {
            "agent": "counterfactual_analyst", "status": "OK",
            "result": {"parameters_to_test": params, "hypothesis": "test", "what_to_watch": "test"},
        }

    def test_rail_dimension_actually_mutates_the_sandboxed_grant(self, monkeypatch):
        captured_dims = {}

        def fake_propose(question, round_result, available_dimensions=None):
            captured_dims["dims"] = available_dimensions
            return self._fake_proposal(available_dimensions or ["AMOUNT"])

        monkeypatch.setattr(A, "propose_counterfactual", fake_propose)

        client = TestClient(app)
        asyncio.run(orchestrator.run_round_stream(round_number=2, dtl_enabled=True, speed=100.0))  # CROSS_RAIL_SPLIT: no fixed profile

        r = client.post("/api/ai/counterfactual", json={"question": "what if?"})
        assert r.status_code == 200
        body = r.json()
        assert captured_dims["dims"] == ["AMOUNT", "RAIL", "PURPOSE"]

        runs = body["simulated_outcomes"]
        by_dim = {r["dimension"]: r for r in runs}
        assert set(by_dim) == {"AMOUNT", "RAIL", "PURPOSE"}
        assert by_dim["RAIL"]["parameter_summary"] == "CARD_TOKEN disabled"
        # Proof the mutation actually took effect in the sandbox, not just an
        # echo of the request: CARD_TOKEN really is absent from the grant the
        # round was replayed against.
        assert "CARD_TOKEN" not in by_dim["RAIL"]["permitted_rails_tested"]
        assert "UPI_CIRCLE" in by_dim["RAIL"]["permitted_rails_tested"]

        assert by_dim["PURPOSE"]["parameter_summary"] == "Gift cards / stored value permitted"
        assert "GIFT_CARD" not in by_dim["PURPOSE"]["semantic_exclusions_tested"]
        assert "STORED_VALUE" not in by_dim["PURPOSE"]["semantic_exclusions_tested"]

    def test_fixed_profile_round_only_gets_amount_dimension(self, monkeypatch):
        captured_dims = {}

        def fake_propose(question, round_result, available_dimensions=None):
            captured_dims["dims"] = available_dimensions
            return self._fake_proposal(available_dimensions or ["AMOUNT"])

        monkeypatch.setattr(A, "propose_counterfactual", fake_propose)

        client = TestClient(app)
        asyncio.run(orchestrator.run_round_stream(round_number=7, dtl_enabled=True, speed=100.0))  # RAIL_SCOPE_VIOLATION: fixed profile

        r = client.post("/api/ai/counterfactual", json={"question": "what if?"})
        assert r.status_code == 200
        assert captured_dims["dims"] == ["AMOUNT"]
        body = r.json()
        assert {run["dimension"] for run in body["simulated_outcomes"]} == {"AMOUNT"}
