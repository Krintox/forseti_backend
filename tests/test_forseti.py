"""
FORSETI test suite.

These tests assert real behaviour of the executable system. Tests that depend on
an optional component (a trained model artifact, a licensed dataset) SKIP with a
clear reason rather than passing vacuously.

    cd backend && python -m pytest tests/ -v
"""

from __future__ import annotations

import asyncio
import json
import os

import pytest

from app.arena.events import EventRecorder
from app.arena.orchestrator import AUTHORITY_ID, ArenaBattleOrchestrator
from app.crypto.canonicalization import CanonicalSerializer
from app.crypto.mldsa_audit import PQCDelegationAuditModule
from app.crypto.pqc_provider import MLDSA44Provider
from app.detector.dataset_builder import SyntheticMLDatasetBuilder
from app.detector.feature_schema import ALL_FEATURE_NAMES, DTLFeatureExtractor
from app.detector.inference import HybridMLDetectorInference
from app.dtl.cost_governor import AdversarialCostGovernor
from app.dtl.invariant_engine import DTLInvariantEngine
from app.dtl.ledger import DTLLedger
from app.models.state import PaymentRailType
from app.models.transactions import CartItem, SyntheticTransaction
from app.paths import METRICS_PATH
from app.redteam.vectors.cross_rail_split import CrossRailSplitVector
from app.redteam.vectors.intent_laundering import IntentLaunderingVector
from app.simulator.state_machine import PaymentSimulatorEngine
from app.taxonomy import TAXONOMY, taxonomy_summary


def _tx(rail=PaymentRailType.CARD_TOKEN, amount=1000.0, mcc="5411", stored_value=False):
    return SyntheticTransaction(
        tx_id=f"tx_test_{rail}_{amount}",
        authority_id=AUTHORITY_ID,
        agent_id="agent_test",
        rail=rail,
        amount=amount,
        merchant_id="m_test",
        merchant_name="Test Mart",
        merchant_mcc=mcc,
        items=[
            CartItem(
                sku="SKU_T", name="Gift Card" if stored_value else "Milk",
                category="GIFT_CARD" if stored_value else "GROCERY",
                unit_price=amount, quantity=1, is_stored_value=stored_value,
            )
        ],
    )


# ----------------------------------------------------------------- DTL


class TestDTL:
    def test_global_budget_invariant_holds_within_ceiling(self):
        ledger = DTLLedger()
        auth = ledger.get_authority(AUTHORITY_ID)
        ok, proof = DTLInvariantEngine().evaluate_invariants(auth, _tx(amount=4000.0))
        assert ok is True and proof is None

    def test_cross_rail_aggregate_breaches_ceiling(self):
        """Three legs legal on their own rail, illegal in aggregate."""
        ledger = DTLLedger()
        engine = DTLInvariantEngine()
        auth = ledger.get_authority(AUTHORITY_ID)
        results = []
        for tx in CrossRailSplitVector.generate_attack(AUTHORITY_ID):
            ok, proof = engine.evaluate_invariants(auth, tx)
            results.append((ok, proof))
            if ok:
                ledger.finalize_authorized_spend(AUTHORITY_ID, tx.amount)

        assert results[0][0] is True, "first 4k must fit inside a 10k grant"
        assert results[1][0] is True, "second 4k still fits (8k of 10k)"
        assert results[2][0] is False, "third 4k must breach the 10k ceiling"
        proof = results[2][1]
        assert proof.invariant_code == "INV_01_GLOBAL_BUDGET_EXCEEDED"
        assert proof.total_exposure_after == pytest.approx(12000.0)

    def test_semantic_drift_detected_on_stored_value(self):
        auth = DTLLedger().get_authority(AUTHORITY_ID)
        ok, proof = DTLInvariantEngine().evaluate_invariants(auth, _tx(amount=500.0, stored_value=True))
        assert ok is False
        assert proof.invariant_code == "INV_02_SEMANTIC_INTENT_DRIFT"

    def test_two_phase_exposure_counts_pending(self):
        ledger = DTLLedger()
        ledger.register_pending_spend(AUTHORITY_ID, 3000.0)
        auth = ledger.get_authority(AUTHORITY_ID)
        assert auth.total_exposure_global == pytest.approx(3000.0)
        assert auth.authority_headroom == pytest.approx(7000.0)

    def test_reset_restores_full_headroom(self):
        ledger = DTLLedger()
        ledger.finalize_authorized_spend(AUTHORITY_ID, 9000.0)
        ledger.reset_authority(AUTHORITY_ID, budget=10000.0)
        auth = ledger.get_authority(AUTHORITY_ID)
        assert auth.total_exposure_global == 0.0
        assert auth.authority_headroom == 10000.0

    def test_containment_is_partial_not_total_block(self):
        """The governor must clear legitimate value, not lock the user out."""
        ledger = DTLLedger()
        auth = ledger.get_authority(AUTHORITY_ID)
        tx = SyntheticTransaction(
            tx_id="tx_mixed", authority_id=AUTHORITY_ID, agent_id="a", rail=PaymentRailType.CARD_TOKEN,
            amount=4000.0, merchant_id="m", merchant_name="Mart", merchant_mcc="5411",
            items=[
                CartItem(sku="G1", name="Groceries", category="GROCERY", unit_price=2500.0, quantity=1),
                CartItem(sku="V1", name="Gift Card", category="GIFT_CARD", unit_price=1500.0,
                         quantity=1, is_stored_value=True),
            ],
        )
        ok, proof = DTLInvariantEngine().evaluate_invariants(auth, tx)
        assert ok is False
        _, action = AdversarialCostGovernor().apply_containment(auth, tx, proof)
        assert "PARTIAL_AUTH" in action
        assert "2500" in action and "1500" in action


# ----------------------------------------------------------- simulator


class TestSimulator:
    def test_each_rail_approves_a_locally_legal_amount(self):
        sim = PaymentSimulatorEngine()
        for rail in (PaymentRailType.CARD_TOKEN, PaymentRailType.UPI_CIRCLE, PaymentRailType.AGENTIC_AP2):
            ok, msg = sim.process_transaction_local(_tx(rail=rail, amount=4000.0))
            assert ok is True, f"{rail} should approve 4000 within its 10k local limit: {msg}"

    def test_rail_declines_beyond_its_own_limit(self):
        sim = PaymentSimulatorEngine()
        ok, msg = sim.process_transaction_local(_tx(amount=15000.0))
        assert ok is False and "REJECT" in msg

    def test_rail_state_resets_between_cycles(self):
        """Regression: adapters used to accumulate spend forever and then decline."""
        sim = PaymentSimulatorEngine()
        assert sim.process_transaction_local(_tx(amount=9000.0))[0] is True
        sim.reset_all_rails()
        ok, msg = sim.process_transaction_local(_tx(amount=9000.0))
        assert ok is True, f"after reset the rail must accept again: {msg}"

    def test_authorize_then_settle_lifecycle(self):
        sim = PaymentSimulatorEngine()
        tx = _tx(amount=1200.0)
        assert sim.process_transaction_local(tx)[0] is True
        assert str(tx.state) in ("TransactionState.AUTHORIZED", "AUTHORIZED")
        assert sim.capture_and_settle(tx.tx_id) is True


# ------------------------------------------------------------ attacks


class TestAttackVectors:
    def test_cross_rail_split_uses_three_distinct_rails(self):
        txs = CrossRailSplitVector.generate_attack(AUTHORITY_ID)
        assert len(txs) == 3
        assert len({t.rail for t in txs}) == 3
        assert sum(t.amount for t in txs) > 10000.0

    def test_intent_laundering_carries_stored_value_under_clean_mcc(self):
        tx = IntentLaunderingVector.generate_attack(AUTHORITY_ID)
        assert tx.merchant_mcc == "5411"
        assert any(i.is_stored_value for i in tx.items)


# ------------------------------------------------------------ features


class TestFeatures:
    def test_extractor_emits_the_declared_schema(self):
        auth = DTLLedger().get_authority(AUTHORITY_ID)
        feats = DTLFeatureExtractor.extract_features(auth, _tx())
        assert set(feats.keys()) == set(ALL_FEATURE_NAMES)
        assert all(isinstance(v, float) for v in feats.values())

    def test_extraction_is_deterministic(self):
        auth = DTLLedger().get_authority(AUTHORITY_ID)
        tx = _tx()
        assert DTLFeatureExtractor.extract_features(auth, tx) == DTLFeatureExtractor.extract_features(auth, tx)

    def test_generated_dataset_is_chronological_and_labelled(self):
        df = SyntheticMLDatasetBuilder(seed=42).generate_trajectory(num_samples=800, fraud_ratio=0.012)
        assert len(df) == 800
        assert df["timestamp_unix"].is_monotonic_increasing
        assert set(df["is_fraud"].unique()) <= {0, 1}

    def test_legitimate_traffic_respects_the_delegated_ceiling(self):
        """Otherwise 'exposure > ceiling' would carry no signal at all."""
        df = SyntheticMLDatasetBuilder(seed=42).generate_trajectory(num_samples=3000, fraud_ratio=0.012)
        legit_over = df[(df["is_fraud"] == 0) & (df["exposure_after_tx_ratio"] > 1.0)]
        assert len(legit_over) == 0, f"{len(legit_over)} legitimate rows breached their own ceiling"

    def test_stored_value_appears_in_both_classes(self):
        """Anti-circularity: stored value must not be a perfect label proxy."""
        df = SyntheticMLDatasetBuilder(seed=42).generate_trajectory(num_samples=4000, fraud_ratio=0.012)
        legit_sv = df[(df["is_fraud"] == 0) & (df["stored_value_item_count"] > 0)]
        assert len(legit_sv) > 0, "no legitimate stored-value baskets - the feature would leak the label"


# ------------------------------------------------------------- model


class TestModel:
    def test_inference_loads_the_trained_artifact(self):
        det = HybridMLDetectorInference()
        if not det.model_loaded:
            pytest.skip(f"model artifact not built: {det.load_error}")
        assert det.status()["feature_count"] == len(ALL_FEATURE_NAMES)

    def test_probability_is_bounded_and_deterministic(self):
        det = HybridMLDetectorInference()
        if not det.model_loaded:
            pytest.skip("model artifact not built")
        auth = DTLLedger().get_authority(AUTHORITY_ID)
        tx = _tx(amount=3000.0)
        p1, _, _ = det.evaluate_transaction(auth, tx)
        p2, _, _ = det.evaluate_transaction(auth, tx)
        assert 0.0 <= p1 <= 1.0
        assert p1 == p2

    def test_metrics_artifact_records_holdout_and_provenance(self):
        if not os.path.exists(METRICS_PATH):
            pytest.skip("metrics.json not generated")
        m = json.load(open(METRICS_PATH, encoding="utf-8"))
        assert m["attack_family_holdout"]["families_held_out_during_training"]
        assert m["environment"]["seed"] is not None
        assert 0.0 <= m["test_metrics"]["pr_auc"] <= 1.0

    def test_explainability_is_labelled_honestly(self):
        det = HybridMLDetectorInference()
        if not det.model_loaded:
            pytest.skip("model artifact not built")
        auth = DTLLedger().get_authority(AUTHORITY_ID)
        _, _, expl = det.evaluate_transaction(auth, _tx(amount=3000.0), explain=True)
        assert "method" in expl
        # The fallback must never be presented as SHAP.
        if not expl.get("is_genuine_shap"):
            assert expl["method"] != "shap.TreeExplainer"


# --------------------------------------------------------------- PQC


class TestPQC:
    def test_provider_reports_availability_truthfully(self):
        info = MLDSA44Provider.provider_info()
        assert info["algorithm"] == "NIST FIPS 204 ML-DSA-44"
        if not info["available"]:
            assert info["unavailable_reason"]

    def test_sign_and_verify_roundtrip(self):
        if not MLDSA44Provider.AVAILABLE:
            pytest.skip(MLDSA44Provider.UNAVAILABLE_REASON or "PQC unavailable")
        pk, sk = MLDSA44Provider.generate_keypair(b"test-seed")
        assert len(pk) == 1312 and len(sk) == 2560
        sig = MLDSA44Provider.sign(b"authority-snapshot", sk)
        assert len(sig) == 2420
        assert MLDSA44Provider.verify(b"authority-snapshot", sig, pk) is True

    def test_modified_message_fails_verification(self):
        if not MLDSA44Provider.AVAILABLE:
            pytest.skip("PQC unavailable")
        pk, sk = MLDSA44Provider.generate_keypair(b"test-seed")
        sig = MLDSA44Provider.sign(b"original", sk)
        assert MLDSA44Provider.verify(b"modified", sig, pk) is False

    def test_modified_signature_fails_verification(self):
        if not MLDSA44Provider.AVAILABLE:
            pytest.skip("PQC unavailable")
        pk, sk = MLDSA44Provider.generate_keypair(b"test-seed")
        sig = bytearray(MLDSA44Provider.sign(b"msg", sk))
        sig[7] ^= 0x01
        assert MLDSA44Provider.verify(b"msg", bytes(sig), pk) is False

    def test_canonical_bytes_are_stable_across_a_browser_json_roundtrip(self):
        """
        RFC 8785 defines canonical number formatting to match ECMAScript's
        Number-to-String rules: an integral value never prints a trailing
        ".0". Python's json.dumps does not follow that rule on its own, so a
        payload signed as {"x": 10000.0} previously produced different
        canonical bytes than the same payload after round-tripping through a
        browser's JSON.parse/JSON.stringify (which collapses 10000.0 to
        10000) - making a completely untampered snapshot fail live
        re-verification the moment any field held a whole-number float.
        """
        payload = {"global_budget_ceiling": 10000.0, "total_exposure": 0.0, "note": "x"}
        python_bytes = CanonicalSerializer.canonical_bytes(payload)

        # Simulates exactly what a browser does to a whole-number float.
        browser_roundtripped = {"global_budget_ceiling": 10000, "total_exposure": 0, "note": "x"}
        browser_bytes = CanonicalSerializer.canonical_bytes(browser_roundtripped)

        assert python_bytes == browser_bytes
        assert b"10000.0" not in python_bytes, "canonical form must not depend on Python's float repr"

    def test_signed_snapshot_survives_a_browser_json_roundtrip(self):
        """End-to-end: sign with Python-typed floats, verify with the same
        payload after simulating JS's int/float collapse - must still pass."""
        if not MLDSA44Provider.AVAILABLE:
            pytest.skip(MLDSA44Provider.UNAVAILABLE_REASON or "PQC unavailable")
        module = PQCDelegationAuditModule()
        auth_state = {
            "authority_id": "auth_test", "principal": "user_test",
            "global_budget_ceiling": 10000.0, "total_exposure_global": 0.0,
            "active_policy": "STANDARD",
        }
        signed = module.create_signed_snapshot(auth_state)
        assert signed["verification_status"] == "ML-DSA-44 VERIFIED"

        js_like_payload = dict(signed["snapshot_payload"])
        js_like_payload["global_budget_ceiling"] = int(js_like_payload["global_budget_ceiling"])
        js_like_payload["total_exposure"] = int(js_like_payload["total_exposure"])

        assert module.verify_snapshot(js_like_payload, signed["signature_hex"]) is True

    def test_active_policy_is_not_the_python_enum_repr(self):
        """
        A live DefensePolicy Enum member passed straight into build_snapshot_payload
        must contribute its .value ("STANDARD"), never str(member)
        ("DefensePolicy.STANDARD") - the same leaked-repr bug fixed elsewhere
        in the app via a `_policy_name()` sanitizer, which this code path had
        not been given.
        """
        from app.models.state import DefensePolicy

        module = PQCDelegationAuditModule()
        payload = module.build_snapshot_payload({
            "authority_id": "a", "principal": "p",
            "global_budget_ceiling": 1000.0, "total_exposure_global": 0.0,
            "active_policy": DefensePolicy.STRICT_INVARIANT,
        })
        assert payload["active_policy"] == "STRICT_INVARIANT"
        assert "DefensePolicy." not in payload["active_policy"]

    def test_wrong_key_fails_verification(self):
        if not MLDSA44Provider.AVAILABLE:
            pytest.skip("PQC unavailable")
        pk1, sk1 = MLDSA44Provider.generate_keypair(b"key-one")
        pk2, _ = MLDSA44Provider.generate_keypair(b"key-two")
        sig = MLDSA44Provider.sign(b"msg", sk1)
        assert MLDSA44Provider.verify(b"msg", sig, pk2) is False

    def test_audit_module_never_claims_verified_when_unavailable(self):
        module = PQCDelegationAuditModule()
        auth = DTLLedger().get_authority(AUTHORITY_ID)
        snap = module.create_signed_snapshot(auth.model_dump())
        if module.available:
            assert snap["verification_status"] == "ML-DSA-44 VERIFIED"
            assert module.run_tamper_test(auth.model_dump())["all_tamper_tests_passed"] is True
        else:
            assert snap["verification_status"] == "PQC MODULE UNAVAILABLE"
            assert snap["is_cryptographically_valid"] is False

    def test_canonicalization_is_order_independent(self):
        a = CanonicalSerializer.canonical_bytes({"b": 2, "a": 1})
        b = CanonicalSerializer.canonical_bytes({"a": 1, "b": 2})
        assert a == b


# ------------------------------------------------------------- arena


class TestArena:
    def test_round_emits_the_full_event_vocabulary(self):
        orch = ArenaBattleOrchestrator()
        orch.reset(10000.0)
        seen = []

        async def cb(evt):
            seen.append(evt["event_type"])

        result = asyncio.run(
            orch.run_round_stream(2, True, cb, speed=50.0, strategy_override="CROSS_RAIL_SPLIT")
        )
        for required in ("ROUND_STARTED", "ATTACK_STARTED", "ATTACK_STEP", "RAIL_REQUEST",
                         "DTL_EVALUATION", "INVARIANT_VIOLATION", "ML_SCORE",
                         "POLICY_DECISION", "PQC_SIGN", "PQC_VERIFY", "ATTACK_COMPLETE"):
            assert required in seen, f"missing event type {required}"
        assert result["winner"] == "BLUE"
        assert result["detected"] is True

    def test_events_are_json_serializable(self):
        """Regression: a datetime in a frame used to kill the WebSocket."""
        orch = ArenaBattleOrchestrator()
        orch.reset(10000.0)
        frames = []

        async def cb(evt):
            frames.append(evt)

        asyncio.run(orch.run_round_stream(2, True, cb, speed=50.0))
        for frame in frames:
            json.dumps(frame)  # must not raise
        json.dumps(orch.get_state())

    def test_all_rails_approve_before_the_dtl_objects(self):
        orch = ArenaBattleOrchestrator()
        orch.reset(10000.0)
        result = asyncio.run(
            orch.run_round_stream(2, True, None, speed=50.0, strategy_override="CROSS_RAIL_SPLIT")
        )
        verdicts = [s["local_rail_verdict"] for s in result["step_results"]]
        assert verdicts == ["APPROVED_BY_RAIL"] * 3, verdicts
        assert any(s["dtl_defense_status"] == "CONTAINED_BY_DTL" for s in result["step_results"])

    def test_event_log_is_hash_chained(self):
        """The log must be tamper-evident, not merely append-only."""
        orch = ArenaBattleOrchestrator()
        orch.reset(10000.0)
        asyncio.run(orch.run_round_stream(2, True, None, speed=50.0))
        events = orch.recorder.timeline()
        assert len(events) > 10
        assert EventRecorder.verify_chain(events)["valid"] is True

    def test_tampering_with_the_log_is_detected(self):
        import copy

        orch = ArenaBattleOrchestrator()
        orch.reset(10000.0)
        asyncio.run(orch.run_round_stream(2, True, None, speed=50.0))
        tampered = copy.deepcopy(orch.recorder.timeline())
        tampered[5]["payload"]["amount"] = 999999
        result = EventRecorder.verify_chain(tampered)
        assert result["valid"] is False
        assert result["broken_at_index"] == 5

    def test_pqc_signature_commits_to_the_event_history(self):
        """Signing a placeholder root would commit to no history at all."""
        orch = ArenaBattleOrchestrator()
        orch.reset(10000.0)
        result = asyncio.run(orch.run_round_stream(2, True, None, speed=50.0))
        if not orch.pqc_module.available:
            pytest.skip("PQC unavailable")
        signed_root = result["pqc_audit"]["snapshot_payload"]["event_root"]
        events = orch.recorder.timeline()
        sign_idx = next(i for i, e in enumerate(events) if e["event_type"] == "PQC_SIGN")
        assert signed_root == events[sign_idx - 1]["entry_hash"]
        assert signed_root != "0x0"

    def test_manual_limit_change_moves_the_headroom(self):
        orch = ArenaBattleOrchestrator()
        orch.reset(10000.0)
        state = orch.set_delegated_limit(25000.0)
        assert state["authority_state"]["global_budget_ceiling"] == 25000.0
        assert state["authority_state"]["authority_headroom"] == 25000.0

    def test_disabling_dtl_lets_the_attack_through(self):
        """The control condition for the whole demonstration."""
        orch = ArenaBattleOrchestrator()
        orch.reset(10000.0)
        result = asyncio.run(
            orch.run_round_stream(2, False, None, speed=50.0, strategy_override="CROSS_RAIL_SPLIT")
        )
        assert result["detected"] is False
        assert result["winner"] == "RED"
        assert result["authority_state"]["total_exposure_global"] > 10000.0

    def test_red_agent_adapts_after_containment(self):
        orch = ArenaBattleOrchestrator()
        orch.reset(10000.0)
        asyncio.run(orch.run_round_stream(2, True, None, speed=50.0, strategy_override="CROSS_RAIL_SPLIT"))
        plan = orch.feedback_engine.plan_next_strategy()
        assert plan["next_strategy"] != "CROSS_RAIL_SPLIT", "must pivot away from a contained strategy"
        assert plan["scoring_table"], "the pivot must be explained by a scoring table"


# ---------------------------------------------------------- taxonomy


class TestTaxonomy:
    def test_all_55_vectors_parse(self):
        assert len(TAXONOMY) == 55

    def test_implemented_vectors_are_marked_separately(self):
        summary = taxonomy_summary()
        # 6 original + 3 attacking the non-monetary authority dimensions.
        assert summary["implemented_count"] == 9
        assert summary["research_only_count"] == 46
        for v in TAXONOMY:
            if v["implemented"]:
                assert v["implementation_module"] and v["strategy_key"]
            else:
                assert v["simulation_status"].startswith("RESEARCH ONLY")


# ---------------------------------------------------------- fidelity


class TestFidelity:
    def test_missing_anchor_is_reported_not_faked(self):
        from app.fidelity.loader import FidelityDatasetLoader

        loader = FidelityDatasetLoader(dataset_dir=os.path.join(os.path.dirname(__file__), "_no_such_dir"))
        data, status = loader.load_paysim()
        assert data is None
        assert "not found" in status.lower()

    def test_ks_test_runs_on_synthetic_data(self):
        from app.fidelity.ks_test import KolmogorovSmirnovTest
        from app.fidelity.loader import FidelityDatasetLoader

        txs = FidelityDatasetLoader().load_synthetic(num_samples=400, seed=42)
        mid = len(txs) // 2
        res = KolmogorovSmirnovTest.calculate_ks(txs[:mid], txs[mid:])
        assert 0.0 <= res["statistic"] <= 1.0
        assert 0.0 <= res["p_value"] <= 1.0


# ------------------------------------------------------------------ AI layer


class TestAILayer:
    """
    The AI layer must be honest and must never enforce. These tests hold when a
    provider answers AND when none does.
    """

    def test_client_reports_availability_truthfully(self):
        from app.ai.llm_client import get_client

        st = get_client().status()
        assert isinstance(st["available"], bool)
        if not st["available"]:
            assert st["keys_live"] == 0
        assert "never decides" in st["enforcement_note"]

    def test_every_agent_is_catalogued_with_its_problem(self):
        from app.ai.agents import AGENT_CATALOG

        assert len(AGENT_CATALOG) >= 12
        for a in AGENT_CATALOG:
            assert a["problem"] and a["solves"], f"{a['id']} lacks a stated problem"

    def test_deterministic_explanation_needs_no_model(self):
        """The clickable log must never be empty, even with no LLM at all."""
        from app.ai.agents import deterministic_event_explanation

        out = deterministic_event_explanation({
            "event_type": "INVARIANT_VIOLATION", "actor": "DTL", "step": 3,
            "arrow_label": "VIOLATION",
            "payload": {"invariant_code": "INV_01_GLOBAL_BUDGET_EXCEEDED",
                        "exposure_after": 12000, "ceiling": 10000},
        })
        assert out["team"] == "BLUE"
        assert "12,000" in out["what_happened"]
        assert out["source"] == "deterministic_template"

    def test_json_extraction_survives_fenced_output(self):
        from app.ai.llm_client import parse_json_object

        assert parse_json_object('```json\n{"a": 1}\n```')["a"] == 1
        assert parse_json_object('Sure! {"b": 2} hope that helps')["b"] == 2
        with pytest.raises(ValueError):
            parse_json_object("no object here")

    def test_intent_validator_rejects_unknown_mcc(self):
        """A hallucinated category must never widen a policy."""
        from app.ai.agents import KNOWN_MCCS

        assert "9999" not in KNOWN_MCCS
        assert "5411" in KNOWN_MCCS

    def test_log_filter_is_deterministic_and_soft_on_text(self):
        from app.ai.agents import apply_log_filter

        events = [
            {"event_type": "RAIL_APPROVED", "actor": "CARD_RAIL", "severity": "warning",
             "arrow_label": "LOCALLY APPROVED", "payload": {"amount": 4000}},
            {"event_type": "ML_SCORE", "actor": "ML_DETECTOR", "severity": "info",
             "arrow_label": "ML RISK", "payload": {"probability": 0.1}},
        ]
        r = apply_log_filter(events, {"event_types": ["RAIL_APPROVED"]})
        assert len(r["events"]) == 1

        # A literal term that matches nothing must not zero a valid filter.
        r2 = apply_log_filter(events, {"event_types": ["RAIL_APPROVED"],
                                       "text_contains": "objection"})
        assert len(r2["events"]) == 1
        assert r2["text_term_dropped"] == "objection"

    def test_agents_never_raise_to_the_caller(self):
        """Any provider failure must arrive as an envelope, not an exception."""
        from app.ai import agents as A
        from app.ai.llm_client import LLMClient

        original = A.get_client
        try:
            A.get_client = lambda: LLMClient(enabled=False)  # forces LLMUnavailable
            out = A.compile_intent("spend up to 5000 on groceries")
            assert out["status"] in ("LLM_UNAVAILABLE", "FALLBACK")
            assert out["result"] is None

            explained = A.explain_event({"event_type": "ATTACK_STEP", "actor": "RED_AGENT",
                                         "step": 1, "arrow_label": "STEP 1",
                                         "payload": {"amount": 4000, "rail": "CARD_TOKEN"}})
            # This one has a deterministic fallback, so it still returns content.
            assert explained["status"] == "FALLBACK"
            assert explained["result"]["what_happened"]
        finally:
            A.get_client = original
