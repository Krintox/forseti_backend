from typing import Dict, Any, Tuple
from ..models.state import DefensePolicy, DTLGlobalAuthorityState

class BluePolicyAdapter:
    """
    Blue Team Policy Adaptation Engine.
    Dynamically hardens global authority rules based on observed attack history.
    """
    @staticmethod
    def adapt_policy(auth: DTLGlobalAuthorityState, invariant_code: str) -> Tuple[str, Dict[str, Any]]:
        changes = {}
        if invariant_code == "INV_01_GLOBAL_BUDGET_EXCEEDED":
            # Tighten headroom buffer by 10%
            auth.active_policy = DefensePolicy.TIGHTENED_HEADROOM_V2
            changes["headroom_buffer_pct"] = 0.10
            changes["policy"] = DefensePolicy.TIGHTENED_HEADROOM_V2.value
            action_desc = "Reduced available authority headroom buffer by 10% across all rails."
        elif invariant_code == "INV_02_SEMANTIC_INTENT_DRIFT":
            # Blacklist SKU category and force item-level attestation
            auth.active_policy = DefensePolicy.STRICT_CATALOG_ATTESTATION
            changes["require_sku_attestation"] = True
            changes["policy"] = DefensePolicy.STRICT_CATALOG_ATTESTATION.value
            action_desc = "Enacted strict item-level catalog attestation; shadow-quarantined liquid stored value."
        else:
            auth.active_policy = DefensePolicy.STEP_UP_VERIFICATION
            changes["require_step_up"] = True
            changes["policy"] = DefensePolicy.STEP_UP_VERIFICATION.value
            action_desc = "Enforced secondary biometric step-up requirement."

        return action_desc, changes
