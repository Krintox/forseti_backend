"""
Tests for Module 1 of the Agentic Payment Security Runtime expansion:
the BENEFICIARY authority dimension (INV_07) and the Intent Firewall
drift-vector / verdict layer built on top of it.

The Intent Firewall does not duplicate DTLInvariantEngine's checks - it
reshapes the SAME SemanticDriftProof objects into a per-dimension drift
vector and a verdict. These tests pin both layers: that BENEFICIARY behaves
like the other five dimensions (an agent can stay inside money/rail/merchant
scope and still violate it), and that the firewall's verdict tracks severity
correctly.
"""

import pytest

from app.dtl.cost_governor import AdversarialCostGovernor
from app.dtl.invariant_engine import DTLInvariantEngine
from app.dtl.ledger import DTLLedger
from app.intent_firewall import compute_drift_vector, evaluate
from app.models.state import PaymentRailType, TransactionState
from app.models.transactions import CartItem, SyntheticTransaction
from app.redteam.vectors.beneficiary_drift import BeneficiaryDriftVector

AUTHORITY_ID = "auth_household_grocery_2026"


def _tx(rail=PaymentRailType.UPI_CIRCLE, amount=1000.0, mcc="5411", vpa=None):
    return SyntheticTransaction(
        tx_id="tx_beneficiary_test",
        authority_id=AUTHORITY_ID,
        agent_id="agent_test",
        rail=rail,
        amount=amount,
        merchant_id="m_test",
        merchant_name="Test Utility Co",
        merchant_mcc=mcc,
        vpa_delegate=vpa,
        items=[CartItem(sku="SKU_T", name="Bill Payment", category="UTILITIES",
                         unit_price=amount, quantity=1)],
    )


class TestBeneficiaryDimension:
    """INV_07 - the dimension a rail/MCC/amount check cannot express."""

    def test_unauthorized_beneficiary_is_rejected_even_with_full_headroom(self):
        ledger = DTLLedger()
        ledger.reset_authority(
            AUTHORITY_ID, budget=6000.0,
            profile={"beneficiary_scope": ["vpa_electricity_board@upi"]},
        )
        auth = ledger.get_authority(AUTHORITY_ID)
        assert auth.authority_headroom == pytest.approx(6000.0)

        ok, proof = DTLInvariantEngine().evaluate_invariants(
            auth, _tx(vpa="vpa_regional-collections-utility@upi")
        )
        assert ok is False
        assert proof.invariant_code == "INV_07_UNAUTHORIZED_BENEFICIARY"
        assert proof.authority_dimension == "BENEFICIARY"

    def test_permitted_beneficiary_still_passes_under_the_same_grant(self):
        ledger = DTLLedger()
        ledger.reset_authority(
            AUTHORITY_ID, budget=6000.0,
            profile={"beneficiary_scope": ["vpa_electricity_board@upi"]},
        )
        auth = ledger.get_authority(AUTHORITY_ID)
        ok, proof = DTLInvariantEngine().evaluate_invariants(
            auth, _tx(vpa="vpa_electricity_board@upi")
        )
        assert ok is True and proof is None

    def test_unconstrained_beneficiary_scope_allows_anything(self):
        """Empty beneficiary_scope (the default) means unconstrained, same as permitted_mccs."""
        ledger = DTLLedger()
        ledger.reset_authority(AUTHORITY_ID, budget=6000.0)
        auth = ledger.get_authority(AUTHORITY_ID)
        assert auth.beneficiary_scope == []
        ok, proof = DTLInvariantEngine().evaluate_invariants(auth, _tx(vpa="literally_anyone@upi"))
        assert ok is True and proof is None

    def test_authority_vector_includes_beneficiary_row(self):
        ledger = DTLLedger()
        ledger.reset_authority(
            AUTHORITY_ID, budget=6000.0,
            profile={"beneficiary_scope": ["vpa_electricity_board@upi"]},
        )
        auth = ledger.get_authority(AUTHORITY_ID)
        vector = auth.authority_vector()
        assert vector["BENEFICIARY"]["invariant"] == "INV_07_UNAUTHORIZED_BENEFICIARY"
        assert vector["BENEFICIARY"]["granted"] == ["vpa_electricity_board@upi"]
        assert vector["BENEFICIARY"]["unconstrained"] is False

    def test_registry_includes_inv_07_without_disturbing_the_original_six(self):
        registry = DTLInvariantEngine.registry()
        codes = [row["code"] for row in registry]
        assert "INV_07_UNAUTHORIZED_BENEFICIARY" in codes
        for original in (
            "INV_01_GLOBAL_BUDGET_EXCEEDED", "INV_02_SEMANTIC_INTENT_DRIFT",
            "INV_03_UNAUTHORIZED_MCC", "INV_04_UNAUTHORIZED_RAIL",
            "INV_05_PER_TX_CAP_EXCEEDED", "INV_06_AUTHORITY_EXPIRED",
        ):
            assert original in codes


class TestBeneficiaryContainment:
    """
    Regression test for a real gap found while building Module 5: the cost
    governor had no dedicated branch for INV_07 and silently fell through to
    the generic SHADOW_QUARANTINE message, never hardening active_policy -
    unlike every other invariant's dedicated containment branch.
    """

    def test_beneficiary_containment_consumes_no_headroom(self):
        ledger = DTLLedger()
        ledger.reset_authority(
            AUTHORITY_ID, budget=6000.0,
            profile={"beneficiary_scope": ["vpa_electricity_board@upi"]},
        )
        auth = ledger.get_authority(AUTHORITY_ID)
        tx = _tx(vpa="vpa_regional-collections-utility@upi")
        _, proof = DTLInvariantEngine().evaluate_invariants(auth, tx)

        contained, action = AdversarialCostGovernor().apply_containment(auth, tx, proof)
        assert contained.state == TransactionState.QUARANTINED
        assert "BENEFICIARY_SCOPE_BLOCK" in action
        assert auth.authority_headroom == pytest.approx(6000.0), "refusing must not book spend"
        assert str(auth.active_policy.value) == "STRICT_INVARIANT"


class TestBeneficiaryDriftVector:
    """The Attack E red-team vector: legitimate leg + diverted leg."""

    def test_vector_carries_its_own_authority_profile(self):
        assert "beneficiary_scope" in BeneficiaryDriftVector.authority_profile
        assert BeneficiaryDriftVector.authority_profile["beneficiary_scope"] == [
            "vpa_electricity_board@upi"
        ]

    def test_legitimate_leg_passes_diverted_leg_fails(self):
        ledger = DTLLedger()
        auth = ledger.reset_authority(
            AUTHORITY_ID,
            budget=BeneficiaryDriftVector.authority_profile["global_budget_ceiling"],
            profile=BeneficiaryDriftVector.authority_profile,
        )
        txs = BeneficiaryDriftVector.generate_attack(AUTHORITY_ID)
        engine = DTLInvariantEngine()

        legit_ok, legit_proof = engine.evaluate_invariants(auth, txs[0])
        assert legit_ok is True and legit_proof is None

        diverted_ok, diverted_proof = engine.evaluate_invariants(auth, txs[1])
        assert diverted_ok is False
        assert diverted_proof.invariant_code == "INV_07_UNAUTHORIZED_BENEFICIARY"

        # Rail, MCC and amount are all independently in scope on the diverted
        # leg - only the beneficiary dimension catches it.
        assert auth.allows_rail(txs[1].rail)
        assert txs[1].merchant_mcc in auth.permitted_mccs
        assert txs[1].amount <= auth.global_budget_ceiling


class TestDriftEngine:
    def test_no_proofs_yields_zeroed_vector(self):
        vector = compute_drift_vector("tx_1", [])
        assert vector["overall_drift_score"] == 0.0
        assert vector["violating_dimensions"] == []
        assert all(v == 0.0 for v in vector["drift_breakdown"].values())

    def test_proof_populates_its_own_dimension_only(self):
        ledger = DTLLedger()
        ledger.reset_authority(
            AUTHORITY_ID, budget=6000.0,
            profile={"beneficiary_scope": ["vpa_electricity_board@upi"]},
        )
        auth = ledger.get_authority(AUTHORITY_ID)
        _, proof = DTLInvariantEngine().evaluate_invariants(
            auth, _tx(vpa="vpa_regional-collections-utility@upi")
        )
        vector = compute_drift_vector(proof.tx_id, [proof])
        assert vector["drift_breakdown"]["beneficiary_drift"] > 0.0
        assert vector["drift_breakdown"]["amount_drift"] == 0.0
        assert vector["drift_breakdown"]["rail_drift"] == 0.0
        assert vector["violating_dimensions"] == ["beneficiary_drift"]
        assert vector["overall_drift_score"] == vector["drift_breakdown"]["beneficiary_drift"]


class TestFirewallVerdict:
    def test_no_violations_is_allow(self):
        result = evaluate("tx_1", [])
        assert result["verdict"] == "ALLOW"

    def test_high_severity_violation_is_hard_drift(self):
        ledger = DTLLedger()
        ledger.reset_authority(
            AUTHORITY_ID, budget=6000.0,
            profile={"beneficiary_scope": ["vpa_electricity_board@upi"]},
        )
        auth = ledger.get_authority(AUTHORITY_ID)
        _, proof = DTLInvariantEngine().evaluate_invariants(
            auth, _tx(vpa="vpa_regional-collections-utility@upi")
        )
        result = evaluate(proof.tx_id, [proof])
        # INV_07 is registered HIGH severity.
        assert result["verdict"] == "HARD_DRIFT"

    def test_medium_severity_only_violation_is_partial_drift(self):
        ledger = DTLLedger()
        ledger.reset_authority(AUTHORITY_ID, budget=12000.0,
                               profile={"per_transaction_cap": 3000.0})
        auth = ledger.get_authority(AUTHORITY_ID)
        _, proof = DTLInvariantEngine().evaluate_invariants(auth, _tx(amount=4000.0))
        assert proof.invariant_code == "INV_05_PER_TX_CAP_EXCEEDED"  # MEDIUM severity
        result = evaluate(proof.tx_id, [proof])
        assert result["verdict"] == "PARTIAL_DRIFT"
