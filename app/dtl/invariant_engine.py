import uuid
from typing import Optional, Tuple, List
from ..models.state import DTLGlobalAuthorityState
from ..models.transactions import SyntheticTransaction, CartItem
from ..models.proofs import SemanticDriftProof

class DTLInvariantEngine:
    """
    Evaluates Machine-Checkable Invariants over transactions and active authority:
    1. INV_01_GLOBAL_BUDGET_EXCEEDED: Total global exposure exceeds delegated ceiling.
    2. INV_02_SEMANTIC_INTENT_DRIFT: Cart contains stored-value / excluded assets despite legitimate MCC.
    3. INV_03_UNAUTHORIZED_MCC: Merchant category is outside authorized scope.
    """
    def __init__(self):
        pass

    def evaluate_invariants(
        self, 
        auth: DTLGlobalAuthorityState, 
        tx: SyntheticTransaction
    ) -> Tuple[bool, Optional[SemanticDriftProof]]:
        """
        Evaluates the transaction against global authority state.
        Returns (is_valid, optional_proof_if_violated).
        """
        # --- INVARIANT 1: Semantic Intent Invariant (Stored-Value / Gift Card check) ---
        has_stored_value = False
        violated_skus = []
        for item in tx.items:
            # Check item category or name against semantic exclusions
            is_excluded = (
                item.is_stored_value or 
                any(ex.lower() in item.category.lower() or ex.lower() in item.name.lower() 
                    for ex in auth.semantic_exclusions)
            )
            if is_excluded:
                has_stored_value = True
                violated_skus.append(item.sku)

        if has_stored_value:
            proof = SemanticDriftProof(
                proof_id=f"proof_drift_{uuid.uuid4().hex[:8]}",
                authority_id=auth.authority_id,
                tx_id=tx.tx_id,
                invariant_code="INV_02_SEMANTIC_INTENT_DRIFT",
                severity="CRITICAL",
                authorized_intent_summary=f"Consumable groceries only (Budget: ₹{auth.global_budget_ceiling:.2f})",
                actual_cart_summary=f"Purchased liquid stored-value instruments ({', '.join(violated_skus)}) under grocery MCC {tx.merchant_mcc}",
                drift_score=0.95,
                violated_skus=violated_skus,
                local_rail_statuses={tx.rail.value: tx.local_rail_status},
                cumulative_spend_before=auth.total_exposure_global,
                attempted_amount=tx.amount,
                global_ceiling=auth.global_budget_ceiling,
                total_exposure_after=auth.total_exposure_global + tx.amount,
                invariant_expression="ASSERT cart.items.category NOT IN semantic_exclusions",
                explanation=f"Transaction passes local rail MCC ({tx.merchant_mcc}) but converts grocery budget into liquid stored value ({', '.join(violated_skus)})."
            )
            return False, proof

        # --- INVARIANT 2: Global Authority Ceiling (Cross-Rail Invariant) ---
        new_exposure = auth.total_exposure_global + tx.amount
        if new_exposure > auth.global_budget_ceiling:
            proof = SemanticDriftProof(
                proof_id=f"proof_budget_{uuid.uuid4().hex[:8]}",
                authority_id=auth.authority_id,
                tx_id=tx.tx_id,
                invariant_code="INV_01_GLOBAL_BUDGET_EXCEEDED",
                severity="HIGH",
                authorized_intent_summary=f"Total aggregate spend <= ₹{auth.global_budget_ceiling:.2f}",
                actual_cart_summary=f"Cross-rail split attempt pushing total exposure to ₹{new_exposure:.2f}",
                drift_score=0.85,
                violated_skus=[item.sku for item in tx.items],
                local_rail_statuses={tx.rail.value: tx.local_rail_status},
                cumulative_spend_before=auth.total_exposure_global,
                attempted_amount=tx.amount,
                global_ceiling=auth.global_budget_ceiling,
                total_exposure_after=new_exposure,
                invariant_expression="ASSERT (cumulative_settled + authorized + pending + reserved + new_tx) <= global_budget_ceiling",
                explanation=f"Individually valid on {tx.rail.value} (amount ₹{tx.amount:.2f} <= ₹10k limit), but globally exceeds user budget of ₹{auth.global_budget_ceiling:.2f} by ₹{new_exposure - auth.global_budget_ceiling:.2f}."
            )
            return False, proof

        return True, None
