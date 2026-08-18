import os
import sys
import unittest
import numpy as np

# Ensure backend path in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.models.state import DTLGlobalAuthorityState, PaymentRailType, TransactionState
from app.models.transactions import SyntheticTransaction, CartItem
from app.dtl.ledger import DTLLedger
from app.dtl.invariant_engine import DTLInvariantEngine
from app.dtl.cost_governor import AdversarialCostGovernor
from app.detector.feature_schema import DTLFeatureExtractor, ALL_FEATURE_NAMES
from app.detector.inference import HybridMLDetectorInference
from app.fidelity.canonical_schema import CanonicalTransaction
from app.fidelity.ks_test import KolmogorovSmirnovTest
from app.fidelity.correlation import CorrelationDistanceTest
from app.crypto.pqc_provider import MLDSA44Provider
from app.crypto.mldsa_audit import PQCDelegationAuditModule
from app.feedback.feedback_engine import ClosedLoopFeedbackEngine

class TestForsetiDefenseLab(unittest.TestCase):
    def setUp(self):
        self.ledger = DTLLedger()
        self.ledger.reset_authority("auth_test_001", budget=10000.0)
        self.auth = self.ledger.get_authority("auth_test_001")
        self.engine = DTLInvariantEngine()
        self.governor = AdversarialCostGovernor()
        self.detector = HybridMLDetectorInference()
        self.pqc = PQCDelegationAuditModule()
        self.feedback = ClosedLoopFeedbackEngine()

    def test_01_two_phase_balance_tracking(self):
        """Test two-phase balance tracking prevents race conditions"""
        self.assertEqual(self.auth.total_exposure_global, 0.0)
        self.auth.pending_spend_global = 3000.0
        self.auth.reserved_spend_global = 2000.0
        self.assertEqual(self.auth.total_exposure_global, 5000.0)
        self.assertEqual(self.auth.authority_headroom, 5000.0)

    def test_02_global_budget_invariant(self):
        """Test INV_01 triggers when cross-rail total exceeds ceiling"""
        tx = SyntheticTransaction(
            tx_id="tx_test_overage",
            authority_id=self.auth.authority_id,
            agent_id=self.auth.agent_id,
            rail=PaymentRailType.CARD_TOKEN,
            amount=12000.0,
            merchant_id="merch_01",
            merchant_name="Store",
            merchant_mcc="5411"
        )
        is_valid, proof = self.engine.evaluate_invariants(self.auth, tx)
        self.assertFalse(is_valid)
        self.assertIsNotNone(proof)
        self.assertEqual(proof.invariant_code, "INV_01_GLOBAL_BUDGET_EXCEEDED")

    def test_03_semantic_drift_invariant(self):
        """Test INV_02 triggers on gift card / liquid stored value in grocery intent"""
        tx = SyntheticTransaction(
            tx_id="tx_test_gift",
            authority_id=self.auth.authority_id,
            agent_id=self.auth.agent_id,
            rail=PaymentRailType.CARD_TOKEN,
            amount=4000.0,
            merchant_id="merch_02",
            merchant_name="Mega Mart",
            merchant_mcc="5411",
            items=[CartItem(sku="SKU_GIFT", name="Amazon Gift Card", category="GIFT_CARD", unit_price=4000.0, quantity=1, is_stored_value=True)]
        )
        is_valid, proof = self.engine.evaluate_invariants(self.auth, tx)
        self.assertFalse(is_valid)
        self.assertIsNotNone(proof)
        self.assertEqual(proof.invariant_code, "INV_02_SEMANTIC_INTENT_DRIFT")

    def test_04_cost_governor_containment(self):
        """Test cost governor applies partial auth and preserves user trust"""
        tx = SyntheticTransaction(
            tx_id="tx_test_gov",
            authority_id=self.auth.authority_id,
            agent_id=self.auth.agent_id,
            rail=PaymentRailType.CARD_TOKEN,
            amount=15000.0,
            merchant_id="merch_03",
            merchant_name="Store",
            merchant_mcc="5411"
        )
        _, proof = self.engine.evaluate_invariants(self.auth, tx)
        contained_tx, msg = self.governor.apply_containment(self.auth, tx, proof)
        self.assertEqual(contained_tx.state, TransactionState.QUARANTINED)

    def test_05_feature_extractor_completeness(self):
        """Test unified feature extractor extracts all 29 dimensions with no NaNs"""
        tx = SyntheticTransaction(
            tx_id="tx_test_feat",
            authority_id=self.auth.authority_id,
            agent_id=self.auth.agent_id,
            rail=PaymentRailType.UPI_CIRCLE,
            amount=2500.0,
            merchant_id="merch_04",
            merchant_name="Supermarket",
            merchant_mcc="5411"
        )
        feats = DTLFeatureExtractor.extract_features(self.auth, tx)
        self.assertEqual(len(feats), len(ALL_FEATURE_NAMES))
        for k, v in feats.items():
            self.assertIsNotNone(v)
            self.assertFalse(np.isnan(v))

    def test_06_ml_inference_and_attribution(self):
        """Test production inference engine produces calibrated probabilities and attributions"""
        tx = SyntheticTransaction(
            tx_id="tx_test_ml",
            authority_id=self.auth.authority_id,
            agent_id=self.auth.agent_id,
            rail=PaymentRailType.CARD_TOKEN,
            amount=4000.0,
            merchant_id="merch_05",
            merchant_name="Store",
            merchant_mcc="5411"
        )
        prob, is_anom, shap_vals = self.detector.evaluate_transaction(self.auth, tx)
        self.assertGreaterEqual(prob, 0.0)
        self.assertLessEqual(prob, 1.0)
        self.assertIsInstance(shap_vals, dict)

    def test_07_nist_mldsa44_cryptography_and_tampering(self):
        """Test NIST FIPS 204 ML-DSA-44 cryptographic signing, verification, and tamper rejection"""
        tamper_results = self.pqc.run_tamper_test(self.auth.dict())
        self.assertTrue(tamper_results["valid_verification"])
        self.assertTrue(tamper_results["tampered_payload_rejected"])
        self.assertTrue(tamper_results["tampered_signature_rejected"])
        self.assertTrue(tamper_results["all_tamper_tests_passed"])

    def test_08_closed_loop_feedback_adaptation(self):
        """Test stateful Red/Blue adaptation cycle"""
        first_plan = self.feedback.get_next_red_action(round_id=1)
        self.assertEqual(first_plan["strategy"], "CROSS_RAIL_SPLIT")

        # Record block
        self.feedback.record_round_outcome(
            round_id=1,
            strategy="CROSS_RAIL_SPLIT",
            target_rails=["CARD_TOKEN", "UPI_CIRCLE"],
            attempted_amount=8000.0,
            is_detected=True,
            detection_score=0.94,
            violating_invariant="INV_01_GLOBAL_BUDGET_EXCEEDED",
            defense_action="PARTIAL_AUTH",
            red_reasoning="Blocked by DTL",
            auth_state=self.auth
        )

        second_plan = self.feedback.get_next_red_action(round_id=2)
        self.assertEqual(second_plan["strategy"], "INTENT_LAUNDERING")
        self.assertIn("CrossRailSplit was blocked", second_plan["reasoning"])

if __name__ == "__main__":
    unittest.main()
