import pytest
from app.dtl.ledger import DTLLedger
from app.dtl.invariant_engine import DTLInvariantEngine
from app.dtl.cost_governor import AdversarialCostGovernor
from app.redteam.vectors.cross_rail_split import CrossRailSplitVector
from app.redteam.vectors.intent_laundering import IntentLaunderingVector
from app.models.state import TransactionState

def test_intent_laundering_detection_and_partial_auth():
    ledger = DTLLedger()
    engine = DTLInvariantEngine()
    governor = AdversarialCostGovernor()
    
    auth = ledger.get_authority("auth_household_grocery_2026")
    tx = IntentLaunderingVector.generate_attack(auth.authority_id)
    
    is_valid, proof = engine.evaluate_invariants(auth, tx)
    assert is_valid is False
    assert proof is not None
    assert proof.invariant_code == "INV_02_SEMANTIC_INTENT_DRIFT"
    assert "SKU_GIFT_AMZN_8500" in proof.violated_skus
    
    # Test graceful containment
    contained_tx, action_msg = governor.apply_containment(auth, tx, proof)
    assert contained_tx.state == TransactionState.QUARANTINED
    assert "PARTIAL_AUTH" in action_msg

def test_cross_rail_split_detection():
    ledger = DTLLedger()
    engine = DTLInvariantEngine()
    
    auth = ledger.get_authority("auth_household_grocery_2026")
    txs = CrossRailSplitVector.generate_attack(auth.authority_id)
    
    # 1st ₹4,000 tx passes
    is_v1, p1 = engine.evaluate_invariants(auth, txs[0])
    assert is_v1 is True
    ledger.finalize_authorized_spend(auth.authority_id, txs[0].amount)
    
    # 2nd ₹4,000 tx passes (cumulative = ₹8,000 <= ₹10,000)
    is_v2, p2 = engine.evaluate_invariants(auth, txs[1])
    assert is_v2 is True
    ledger.finalize_authorized_spend(auth.authority_id, txs[1].amount)
    
    # 3rd ₹4,000 tx FAILS (cumulative = ₹8,000 + ₹4,000 = ₹12,000 > ₹10,000)
    is_v3, p3 = engine.evaluate_invariants(auth, txs[2])
    assert is_v3 is False
    assert p3 is not None
    assert p3.invariant_code == "INV_01_GLOBAL_BUDGET_EXCEEDED"
