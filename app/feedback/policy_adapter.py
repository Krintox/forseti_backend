from typing import Any, Dict, Tuple

from ..models.state import DefensePolicy, DTLGlobalAuthorityState, stricter_policy


class BluePolicyAdapter:
    """
    Blue Team Policy Adaptation Engine.

    Dynamically hardens global authority rules based on observed attack
    history. Response strength ESCALATES with repetition: a first violation
    on a dimension gets the invariant-specific "soft" response (headroom
    tightening, catalog attestation, step-up) that was the entire behaviour
    of this class before the Adaptive Immune System module; a SECOND
    violation on the SAME invariant this session downgrades the agent's
    capability regardless of which dimension it is, and a THIRD or later
    suspends the mandate pending fresh re-consent.

    This is the "Blue hardens policy" half of the closed adversarial loop -
    the Red planner (adaptive_planner.py) already picks its next strategy
    from what has and hasn't worked before; until this, Blue's response to a
    given invariant was the exact same fixed action every time, no matter
    how many times Red had already been caught doing it. A defender that
    reacts identically to the fifth repeat of an attack it already contained
    four times is not really "adapting" - it is a lookup table.
    """

    # Every invariant escalates through the SAME two-rung ladder once it has
    # fired more than once this session; only the FIRST occurrence gets the
    # invariant-specific soft response below.
    _ESCALATION: Dict[int, DefensePolicy] = {
        2: DefensePolicy.CAPABILITY_QUARANTINED,
        3: DefensePolicy.AGENT_SUSPENDED,
    }

    @classmethod
    def adapt_policy(
        cls,
        auth: DTLGlobalAuthorityState,
        invariant_code: str,
        violation_count: int = 1,
    ) -> Tuple[str, Dict[str, Any]]:
        changes: Dict[str, Any] = {"violation_count": violation_count}

        escalated = cls._ESCALATION.get(min(violation_count, 3))
        if escalated is not None:
            # Monotonic: see stricter_policy() in models/state.py.
            escalated = stricter_policy(auth.active_policy, escalated)
            auth.active_policy = escalated
            changes["policy"] = escalated.value
            changes["escalated"] = True
            if escalated == DefensePolicy.CAPABILITY_QUARANTINED:
                action_desc = (
                    f"REPEAT OFFENSE ({violation_count}x {invariant_code}): agent capability "
                    f"downgraded - further transactions on this authority require operator "
                    f"confirmation until re-consent."
                )
            else:
                action_desc = (
                    f"PERSISTENT OFFENSE ({violation_count}x {invariant_code}): mandate suspended "
                    f"pending fresh re-consent from the principal. Soft containment was tried and "
                    f"repeatedly bypassed by strategy changes."
                )
            return action_desc, changes

        changes["escalated"] = False
        if invariant_code == "INV_01_GLOBAL_BUDGET_EXCEEDED":
            auth.active_policy = stricter_policy(auth.active_policy, DefensePolicy.TIGHTENED_HEADROOM_V2)
            changes["headroom_buffer_pct"] = 0.10
            changes["policy"] = DefensePolicy.TIGHTENED_HEADROOM_V2.value
            action_desc = "Reduced available authority headroom buffer by 10% across all rails."
        elif invariant_code == "INV_02_SEMANTIC_INTENT_DRIFT":
            auth.active_policy = stricter_policy(auth.active_policy, DefensePolicy.STRICT_CATALOG_ATTESTATION)
            changes["require_sku_attestation"] = True
            changes["policy"] = DefensePolicy.STRICT_CATALOG_ATTESTATION.value
            action_desc = "Enacted strict item-level catalog attestation; shadow-quarantined liquid stored value."
        else:
            auth.active_policy = stricter_policy(auth.active_policy, DefensePolicy.STEP_UP_VERIFICATION)
            changes["require_step_up"] = True
            changes["policy"] = DefensePolicy.STEP_UP_VERIFICATION.value
            action_desc = "Enforced secondary biometric step-up requirement."

        return action_desc, changes
