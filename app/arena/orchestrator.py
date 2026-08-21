"""
Closed-loop Red vs Blue battle orchestrator.

The previous implementation ran an entire round inside a few microseconds and
pushed every WebSocket event in a single tick, so the frontend received the
whole battle at once and had nothing to animate. This version streams the round
as a paced sequence of structured events that mirror the real backend steps:

    RED plans -> rail authorises locally -> DTL aggregates globally ->
    invariant fires -> ML scores -> SHAP explains -> governor contains ->
    PQC signs -> RED observes and adapts

Pacing is presentation-only. Every value carried in the events is computed by
the real engines; the delays exist so a human can watch causality unfold.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

from ..crypto.mldsa_audit import PQCDelegationAuditModule
from ..detector.inference import HybridMLDetectorInference
from ..dtl.cost_governor import AdversarialCostGovernor
from ..dtl.invariant_engine import DTLInvariantEngine
from ..dtl.ledger import DTLLedger
from ..deception_lab import evaluate_all as evaluate_deception
from ..feedback.feedback_engine import ClosedLoopFeedbackEngine
from ..intent_firewall import firewall_decision
from ..kill_chain import coverage as kill_chain_coverage
from ..kill_chain import score_round as score_kill_chain_round
from ..risk_engine import compute_unified_risk
from ..models.state import DefensePolicy, DTLGlobalAuthorityState, PaymentRailType
from ..models.transactions import SyntheticTransaction
from ..paths import EVENTS_DIR
from ..redteam.vectors.authority_scope import (
    LapsedMandateVector,
    PerTransactionBreachVector,
    RailScopeViolationVector,
)
from ..redteam.vectors.beneficiary_drift import BeneficiaryDriftVector
from ..redteam.vectors.constraint_erosion import ConstraintErosionVector
from ..redteam.vectors.deception import (
    AuthorityImpersonationVector,
    ContextPoisoningVector,
    PromptInjectionVector,
    ToolOutputPoisoningVector,
)
from ..redteam.vectors.cross_rail_split import CrossRailSplitVector
from ..redteam.vectors.intent_laundering import IntentLaunderingVector
from ..redteam.vectors.other_vectors import (
    BaselinePoisoningVector,
    RevocationFloodVector,
    ScopeCreepVector,
    VelocitySpikeVector,
)
from ..simulator.state_machine import PaymentSimulatorEngine
from .events import RAIL_TO_ACTOR, Actor, ArenaEvent, EventRecorder, EventType

AUTHORITY_ID = "auth_household_grocery_2026"

# Hypothetical (counterfactual) rounds are persisted here rather than in the
# main events directory, so they never pollute the replayable round list.
SANDBOX_EVENTS_DIR = os.path.join(EVENTS_DIR, "_sandbox")

STRATEGY_BY_ROUND = {
    1: "INTENT_LAUNDERING",
    2: "CROSS_RAIL_SPLIT",
    3: "BASELINE_POISONING",
    4: "REVOCATION_FLOOD",
    5: "VELOCITY_BURST",
    6: "SCOPE_CREEP",
    7: "RAIL_SCOPE_VIOLATION",
    8: "PER_TX_BREACH",
    9: "LAPSED_MANDATE",
    10: "BENEFICIARY_DRIFT",
    11: "PROMPT_INJECTION",
    12: "TOOL_OUTPUT_POISONING",
    13: "CONTEXT_MEMORY_POISONING",
    14: "AUTHORITY_IMPERSONATION",
    15: "CONSTRAINT_EROSION",
}

STRATEGY_NARRATIVE = {
    "CROSS_RAIL_SPLIT": "Split one over-budget objective across separate payment rails so no single rail sees a violation.",
    "INTENT_LAUNDERING": "Keep the merchant category legitimate while converting the budget into liquid stored value.",
    "BASELINE_POISONING": "Drift the spending baseline gradually so the anomaly threshold moves with the attacker.",
    "REVOCATION_FLOOD": "Race revocation and re-grant so a stale delegation authorises the spend.",
    "VELOCITY_BURST": "Probe with many small transactions that each sit below the review threshold.",
    "SCOPE_CREEP": "Extend the purchase scope past the delegated economic purpose.",
    "RAIL_SCOPE_VIOLATION": "Stay inside the money limit, but use a payment rail the user never authorised.",
    "PER_TX_BREACH": "Keep the aggregate legal while breaking the per-transaction bound the user set.",
    "LAPSED_MANDATE": "Present a perfectly in-scope basket after the delegation's validity window closed.",
    "BENEFICIARY_DRIFT": "Keep rail, amount and merchant category in scope while settling to a beneficiary the grant never named.",
    "PROMPT_INJECTION": "A compromised merchant response tries to talk the agent's own reasoning past its authority.",
    "TOOL_OUTPUT_POISONING": "A product-search tool misreports what a cart actually contains.",
    "CONTEXT_MEMORY_POISONING": "The agent's own context claims a stale, more permissive authorization than the live grant.",
    "AUTHORITY_IMPERSONATION": "A sub-agent records itself as the approver of its own authority escalation.",
    "CONSTRAINT_EROSION": "Spread purpose drift across four escalating legs instead of one obvious spike.",
}

# Which dimension of the delegated authority each vector is designed to attack.
# The flagship cross-rail split is only one row of this table, and saying so is
# what makes the concept defensible: FORSETI protects delegated authority, of
# which the money limit is a single dimension.
STRATEGY_DIMENSION = {
    "CROSS_RAIL_SPLIT": "AMOUNT",
    "INTENT_LAUNDERING": "PURPOSE",
    "SCOPE_CREEP": "MERCHANT",
    "RAIL_SCOPE_VIOLATION": "RAIL",
    "PER_TX_BREACH": "PER_TX",
    "LAPSED_MANDATE": "TIME",
    "BASELINE_POISONING": "AMOUNT",
    "REVOCATION_FLOOD": "TIME",
    "VELOCITY_BURST": "AMOUNT",
    "BENEFICIARY_DRIFT": "BENEFICIARY",
    # These four are NOT authority-dimension attacks - they target the AGENT's
    # own reasoning (see deception_lab/), not the delegated grant. Deception
    # Lab vectors are deliberately built to sit inside every real dimension of
    # authority, so none of the seven values above would be honest here.
    # "AGENT_INTEGRITY" is a display-only label, not an AuthorityDimension
    # enum member - there is no INV_ code for it because deception detection
    # is a parallel concern to authority-dimension enforcement, not a new row
    # in the same table.
    "PROMPT_INJECTION": "AGENT_INTEGRITY",
    "TOOL_OUTPUT_POISONING": "AGENT_INTEGRITY",
    "CONTEXT_MEMORY_POISONING": "AGENT_INTEGRITY",
    "AUTHORITY_IMPERSONATION": "AGENT_INTEGRITY",
    "CONSTRAINT_EROSION": "PURPOSE",
}

# Vectors that only mean something against a specific grant. Running "UPI only"
# against an all-rails delegation would prove nothing, so the arena re-grants
# the authority the way the user would have stated it, emits it as an event, and
# only then lets the Red agent move.
STRATEGY_AUTHORITY_PROFILE = {
    "RAIL_SCOPE_VIOLATION": RailScopeViolationVector.authority_profile,
    "PER_TX_BREACH": PerTransactionBreachVector.authority_profile,
    "LAPSED_MANDATE": LapsedMandateVector.authority_profile,
    "BENEFICIARY_DRIFT": BeneficiaryDriftVector.authority_profile,
    "CONSTRAINT_EROSION": ConstraintErosionVector.authority_profile,
}


def _policy_name(value: Any) -> str:
    """Enum member -> its plain value, so no `DefensePolicy.X` repr reaches the UI."""
    return str(getattr(value, "value", value))

class ArenaBattleOrchestrator:
    """Runs a paced, fully-instrumented attack/defence round."""

    def __init__(self) -> None:
        self.simulator = PaymentSimulatorEngine()
        self.ledger = DTLLedger()
        self.invariant_engine = DTLInvariantEngine()
        self.cost_governor = AdversarialCostGovernor()
        self.detector = HybridMLDetectorInference()
        self.pqc_module = PQCDelegationAuditModule()
        self.feedback_engine = ClosedLoopFeedbackEngine()
        self.recorder = EventRecorder()
        self.speed = 1.0
        self.is_running = False
        self.configured_ceiling = 10000.0
        # Retained so the AI agents can reason over the most recent incident.
        self.last_round: Optional[Dict[str, Any]] = None
        # Intent Firewall verdicts from the most recently completed round, one
        # entry per transaction, newest last.
        self.last_firewall_verdicts: List[Dict[str, Any]] = []
        # Deception Lab verdicts, same shape/lifecycle as the firewall ones.
        self.last_deception_verdicts: List[Dict[str, Any]] = []
        # One kill_chain score per completed round this session, oldest
        # first - the basis for the session-level coverage() rollup.
        self.round_history: List[Dict[str, Any]] = []
        # The grant the OPERATOR configured, as distinct from one a vector
        # profile temporarily imposes. See _apply_operator_grant.
        self.operator_grant: Dict[str, Any] = {"global_budget_ceiling": 10000.0}

    # Dimensions a vector's authority_profile is allowed to override for the
    # duration of its own round.
    _SCOPE_FIELDS = (
        "permitted_rails", "per_transaction_cap", "validity_window_hours",
        "permitted_mccs", "economic_purpose", "semantic_exclusions",
        "beneficiary_scope",
    )

    @classmethod
    def _default_scope(cls) -> Dict[str, Any]:
        """The model's own field defaults, read from a throwaway instance."""
        blank = DTLGlobalAuthorityState(authority_id="_", principal="_", agent_id="_")
        return {f: getattr(blank, f) for f in cls._SCOPE_FIELDS}

    def _apply_operator_grant(self) -> Any:
        """
        Restores the operator's grant before a round that brings no profile of
        its own.

        Vectors like RAIL_SCOPE_VIOLATION and PER_TX_BREACH re-grant the
        authority to demonstrate their dimension ("UPI only", "max ₹3,000 per
        transaction"). Without restoring afterwards, that profile stayed in
        force for every later round: running the full campaign reported
        INV_05_PER_TX_CAP_EXCEEDED for Intent Laundering and Scope Creep
        instead of the semantic-drift and MCC invariants those vectors exist to
        demonstrate. Exposure is deliberately preserved so a multi-vector
        campaign still depletes one shared headroom.
        """
        grant = self._default_scope()
        grant.update(self.operator_grant)
        # allow_none: a full restore must be able to CLEAR an optional
        # dimension (per_transaction_cap back to unconstrained), not just
        # overwrite non-null ones.
        self.ledger.update_authority_scope(AUTHORITY_ID, grant, allow_none=True)
        auth = self.ledger.get_authority(AUTHORITY_ID)
        self.configured_ceiling = auth.global_budget_ceiling
        return auth

    def sandbox(self) -> "ArenaBattleOrchestrator":
        """
        An isolated clone for hypothetical runs (counterfactual "what if the
        limit had been X?" analysis).

        A what-if must never disturb what the operator is actually looking at.
        Running counterfactuals against the LIVE orchestrator previously reset
        the authority between each simulated ceiling, which silently wiped the
        operator's rail scope and per-transaction cap, erased the whole event
        log, and cleared `last_round` - so Incident Report, Policy Advisor and
        Customer Notice all started answering "no round has been executed yet"
        immediately after a counterfactual was run.

        The detector and PQC keys are shared deliberately: they are stateless
        at inference/signing time, and re-loading the model per what-if would
        cost seconds each. Everything that carries mutable round state -
        ledger, simulator, feedback memory, event recorder - is fresh.
        """
        clone = ArenaBattleOrchestrator.__new__(ArenaBattleOrchestrator)
        clone.simulator = PaymentSimulatorEngine()
        clone.ledger = DTLLedger()
        clone.invariant_engine = DTLInvariantEngine()
        clone.cost_governor = AdversarialCostGovernor()
        clone.detector = self.detector            # stateless scoring
        clone.pqc_module = self.pqc_module        # stateless signing
        clone.feedback_engine = ClosedLoopFeedbackEngine()
        # Hypothetical rounds are written to a sandbox directory so they never
        # appear in the operator's "Recorded rounds" replay list. The listing
        # only picks up *.jsonl files, so a subdirectory is ignored by design.
        clone.recorder = EventRecorder(persist_dir=SANDBOX_EVENTS_DIR)
        clone.speed = 100.0
        clone.is_running = False
        clone.configured_ceiling = self.configured_ceiling
        clone.last_round = None
        clone.last_firewall_verdicts = []
        clone.last_deception_verdicts = []
        clone.round_history = []
        return clone

    # ------------------------------------------------------------- state

    def reset(self, budget: Optional[float] = None) -> Dict[str, Any]:
        ceiling = float(budget) if budget is not None else self.configured_ceiling
        self.configured_ceiling = ceiling
        # A reset re-establishes the operator's baseline grant: default scope
        # on every non-monetary dimension, at the requested ceiling.
        self.operator_grant = {"global_budget_ceiling": ceiling}
        self.ledger.reset_authority(AUTHORITY_ID, budget=ceiling)
        self.simulator.reset_all_rails()
        self.feedback_engine.reset()
        self.recorder.reset()
        self.last_round = None
        self.last_firewall_verdicts = []
        self.last_deception_verdicts = []
        self.round_history = []
        self.is_running = False
        return self.get_state()

    def set_delegated_limit(self, amount: float) -> Dict[str, Any]:
        """
        Applies a user-entered delegated ceiling. Exposure already booked is
        preserved so the operator can watch headroom shrink in real time.
        """
        amount = max(0.0, float(amount))
        auth = self.ledger.get_authority(AUTHORITY_ID)
        self.configured_ceiling = amount
        self.operator_grant["global_budget_ceiling"] = amount
        auth.global_budget_ceiling = amount
        auth.last_updated_at = datetime.now(timezone.utc)
        return self.get_state()

    def set_authority_scope(self, profile: Dict[str, Any]) -> Dict[str, Any]:
        """
        Applies operator-entered NON-monetary authority dimensions - permitted
        rails, per-transaction cap, validity window, purpose - on top of the
        live grant. This is what lets an operator actually state "₹12,000,
        UPI only" instead of only ever changing the ceiling.
        """
        auth = self.ledger.update_authority_scope(AUTHORITY_ID, profile)
        # Remember it as the operator's baseline, so a vector profile applied
        # by a later round is reverted back to THIS, not to bare defaults.
        for key, value in profile.items():
            if key in self._SCOPE_FIELDS or key == "global_budget_ceiling":
                self.operator_grant[key] = value
        if auth is not None and "global_budget_ceiling" in profile:
            self.configured_ceiling = auth.global_budget_ceiling
        return self.get_state()

    def get_state(self) -> Dict[str, Any]:
        auth = self.ledger.get_authority(AUTHORITY_ID)
        # mode="json" coerces datetimes and enums to primitives. Without it the
        # WebSocket send_json() raises on datetime fields, which previously
        # killed the socket on its first frame and silently disabled the whole
        # live event stream.
        try:
            state = auth.model_dump(mode="json")
        except AttributeError:  # pydantic v1 fallback
            state = json.loads(auth.json())
        state["total_exposure_global"] = round(auth.total_exposure_global, 2)
        state["authority_headroom"] = round(auth.authority_headroom, 2)
        state["utilization_pct"] = round(
            (auth.total_exposure_global / auth.global_budget_ceiling * 100.0)
            if auth.global_budget_ceiling > 0 else 0.0, 2
        )
        state["permitted_rails"] = auth.permitted_rail_values
        return {
            "authority_state": state,
            "authority_vector": auth.authority_vector(),
            "invariant_registry": self.invariant_engine.registry(),
            "detector_status": self.detector.status(),
            "pqc_status": self.pqc_module.provider_status(),
            "active_policy": str(getattr(auth.active_policy, "value", auth.active_policy)),
            "experiment_id": self.recorder.experiment_id,
            "is_running": self.is_running,
            "feedback_history": self.feedback_engine.get_adaptation_history(),
            "event_count": len(self.recorder.events),
        }

    # ------------------------------------------------------------ helpers

    # Some vectors model a single transaction, others a burst of them, so their
    # generators return either one SyntheticTransaction or a list. Normalising
    # here (rather than at each call site) is what keeps that difference from
    # mattering: hand-wrapping a list-returning generator in another list
    # produced [[tx, tx, ...]], and the round then crashed on `t.amount` with
    # "'list' object has no attribute 'amount'". BASELINE_POISONING,
    # REVOCATION_FLOOD and VELOCITY_BURST were all unreachable that way.
    _VECTORS = {
        "CROSS_RAIL_SPLIT": CrossRailSplitVector,
        "INTENT_LAUNDERING": IntentLaunderingVector,
        "RAIL_SCOPE_VIOLATION": RailScopeViolationVector,
        "PER_TX_BREACH": PerTransactionBreachVector,
        "LAPSED_MANDATE": LapsedMandateVector,
        "BASELINE_POISONING": BaselinePoisoningVector,
        "REVOCATION_FLOOD": RevocationFloodVector,
        "VELOCITY_BURST": VelocitySpikeVector,
        "SCOPE_CREEP": ScopeCreepVector,
        "BENEFICIARY_DRIFT": BeneficiaryDriftVector,
        "PROMPT_INJECTION": PromptInjectionVector,
        "TOOL_OUTPUT_POISONING": ToolOutputPoisoningVector,
        "CONTEXT_MEMORY_POISONING": ContextPoisoningVector,
        "AUTHORITY_IMPERSONATION": AuthorityImpersonationVector,
        "CONSTRAINT_EROSION": ConstraintErosionVector,
    }

    def _select_attack(self, strategy: str) -> List[SyntheticTransaction]:
        vector = self._VECTORS.get(strategy, ScopeCreepVector)
        produced = vector.generate_attack(AUTHORITY_ID)
        txs = list(produced) if isinstance(produced, (list, tuple)) else [produced]
        if not txs or not all(isinstance(t, SyntheticTransaction) for t in txs):
            raise TypeError(
                f"{vector.__name__}.generate_attack must yield SyntheticTransaction(s); "
                f"got {type(produced).__name__}"
            )
        return txs

    @staticmethod
    def _rail_name(tx: SyntheticTransaction) -> str:
        return tx.rail.value if hasattr(tx.rail, "value") else str(tx.rail)

    async def _emit(self, cb: Optional[Callable[[Dict[str, Any]], Awaitable[None]]],
                    event: ArenaEvent, pause: float = 0.0) -> Dict[str, Any]:
        """Records, broadcasts, then waits so the UI can animate the step."""
        data = self.recorder.record(event)
        if cb is not None:
            await cb(data)
        if pause > 0:
            await asyncio.sleep(pause / max(0.1, self.speed))
        return data

    # -------------------------------------------------------------- round

    async def run_round_stream(
        self,
        round_number: int = 2,
        dtl_enabled: bool = True,
        event_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
        speed: float = 1.0,
        strategy_override: Optional[str] = None,
    ) -> Dict[str, Any]:
        self.speed = max(0.1, float(speed))
        self.is_running = True
        auth = self.ledger.get_authority(AUTHORITY_ID)

        # Each rail enforces a PER-CYCLE limit, and a round models one cycle.
        # Without this reset the adapters carried spend across rounds and began
        # declining locally, which silently destroyed the whole demonstration:
        # the point is that every rail approves while only the DTL objects.
        self.simulator.reset_all_rails()

        strategy = strategy_override or STRATEGY_BY_ROUND.get(round_number, "SCOPE_CREEP")

        # Vectors that attack a non-monetary dimension only mean something
        # against a grant that actually states that dimension. Re-grant it here,
        # from the vector's own declared profile, so the picture the judge sees
        # is the delegation the attack was designed against - never an implied one.
        profile = STRATEGY_AUTHORITY_PROFILE.get(strategy)
        if profile:
            auth = self.ledger.reset_authority(
                AUTHORITY_ID,
                budget=float(profile.get("global_budget_ceiling", self.configured_ceiling)),
                profile=profile,
            )
            self.configured_ceiling = auth.global_budget_ceiling
        else:
            # No profile of its own: run against the operator's grant, undoing
            # any scope a previous vector temporarily imposed.
            auth = self._apply_operator_grant()

        attacks = self._select_attack(strategy)
        total_objective = sum(t.amount for t in attacks)
        exp_id = self.recorder.experiment_id

        def ev(**kwargs) -> ArenaEvent:
            kwargs.setdefault("experiment_id", exp_id)
            kwargs.setdefault("round_id", round_number)
            return ArenaEvent(**kwargs)

        vector = auth.authority_vector()
        await self._emit(event_callback, ev(
            event_type=EventType.AUTHORITY_GRANTED, actor=Actor.DTL,
            source="principal", target="dtl",
            arrow_label=(f"USER GRANT: Rs {auth.global_budget_ceiling:,.0f} · "
                         f"{'/'.join(r.split('_')[0] for r in auth.permitted_rail_values) or 'no rail'} · "
                         f"{auth.validity_window_hours:.0f}h"),
            severity="info",
            payload={
                "authority_id": auth.authority_id,
                "principal": auth.principal,
                "agent_id": auth.agent_id,
                "authority_vector": vector,
                "targeted_dimension": STRATEGY_DIMENSION.get(strategy, "AMOUNT"),
                "profile_applied": bool(profile),
                "description": (
                    "The grant, stated in every dimension it has. The attack below targets the "
                    f"{STRATEGY_DIMENSION.get(strategy, 'AMOUNT')} dimension."
                ),
            },
        ), pause=0.4)

        await self._emit(event_callback, ev(
            event_type=EventType.ROUND_STARTED, actor=Actor.SYSTEM,
            arrow_label=f"ROUND {round_number} - {strategy.replace('_', ' ')}",
            severity="info",
            payload={
                "strategy": strategy,
                "narrative": STRATEGY_NARRATIVE.get(strategy, ""),
                "targeted_dimension": STRATEGY_DIMENSION.get(strategy, "AMOUNT"),
                "dtl_enabled": dtl_enabled,
                "delegated_ceiling": auth.global_budget_ceiling,
                "permitted_rails": auth.permitted_rail_values,
                "per_transaction_cap": auth.per_transaction_cap,
                "exposure_before": round(auth.total_exposure_global, 2),
                "headroom_before": round(auth.authority_headroom, 2),
                "planned_steps": len(attacks),
                "total_objective_amount": round(total_objective, 2),
            },
        ), pause=0.45)

        await self._emit(event_callback, ev(
            event_type=EventType.ATTACK_STARTED, actor=Actor.RED_AGENT,
            source="red_agent", target="rails",
            arrow_label=f"PLANNING {strategy.replace('_', ' ')}: Rs {total_objective:,.0f} across {len(attacks)} rail(s)",
            severity="critical",
            payload={
                "strategy": strategy,
                "goal": f"Move Rs {total_objective:,.2f} while each individual rail stays inside its own limit",
                "target_rails": [self._rail_name(t) for t in attacks],
                "total_objective_amount": round(total_objective, 2),
                "delegated_ceiling": auth.global_budget_ceiling,
                "overshoot_amount": round(max(0.0, total_objective - auth.authority_headroom), 2),
            },
        ), pause=0.6)

        step_results: List[Dict[str, Any]] = []
        firewall_verdicts: List[Dict[str, Any]] = []
        deception_verdicts: List[Dict[str, Any]] = []
        last_proof = None
        highest_ml_prob = 0.0

        for idx, tx in enumerate(attacks, start=1):
            rail_name = self._rail_name(tx)
            rail_actor = RAIL_TO_ACTOR.get(rail_name, Actor.SYSTEM)
            exposure_before = auth.total_exposure_global
            headroom_before = auth.authority_headroom

            await self._emit(event_callback, ev(
                event_type=EventType.ATTACK_STEP, actor=Actor.RED_AGENT, step=idx,
                source="red_agent", target=rail_name.lower(),
                arrow_label=f"STEP {idx}: SENDING Rs {tx.amount:,.0f} VIA {rail_name.split('_')[0]}",
                severity="critical",
                payload={
                    "step": idx, "of": len(attacks), "rail": rail_name,
                    "amount": tx.amount, "merchant": tx.merchant_name, "mcc": tx.merchant_mcc,
                    "tx_id": tx.tx_id,
                    "description": f"Red agent routes Rs {tx.amount:,.2f} through {rail_name}",
                },
            ), pause=0.5)

            # ---- local rail decision (each rail only knows its own limit)
            await self._emit(event_callback, ev(
                event_type=EventType.RAIL_REQUEST, actor=rail_actor, step=idx,
                source=rail_name.lower(), target="rail_authorizer",
                arrow_label=f"AUTH REQUEST Rs {tx.amount:,.0f}",
                payload={"rail": rail_name, "amount": tx.amount, "tx_id": tx.tx_id,
                         "local_limit": auth.active_delegations_by_rail.get(tx.rail, 10000.0)},
            ), pause=0.4)

            is_local_auth, local_msg = self.simulator.process_transaction_local(tx)
            local_verdict = "APPROVED_BY_RAIL" if is_local_auth else "DECLINED_BY_RAIL"

            await self._emit(event_callback, ev(
                event_type=EventType.RAIL_APPROVED if is_local_auth else EventType.RAIL_DECLINED,
                actor=rail_actor, step=idx,
                source=rail_name.lower(), target="dtl",
                arrow_label=("LOCALLY APPROVED - LOOKS NORMAL" if is_local_auth
                             else f"DECLINED BY RAIL: {local_msg}"),
                severity="warning" if is_local_auth else "success",
                payload={
                    "rail": rail_name, "amount": tx.amount, "tx_id": tx.tx_id,
                    "verdict": local_verdict, "message": local_msg,
                    "why_it_passes": ("This rail only checks its own limit. It cannot see spend on the other rails."
                                      if is_local_auth else None),
                },
            ), pause=0.45)

            # ---- Deception Lab: does not depend on dtl_enabled or on any
            # authority-dimension violation. This layer asks whether the AGENT
            # was fed a false premise (injected instruction, poisoned tool
            # result, fabricated memory, self-issued escalation) - orthogonal
            # to whether the resulting action happens to be inside the grant.
            deception_proofs = evaluate_deception(auth, tx)
            deception_verdict = {
                "tx_id": tx.tx_id,
                "verdict": "DECEPTION_DETECTED" if deception_proofs else "CLEAN",
                "detections": [
                    {"type": p.deception_type, "severity": p.severity,
                     "deceptive_input": p.deceptive_input,
                     "ground_truth_check": p.ground_truth_check,
                     "explanation": p.explanation, "proof_id": p.proof_id}
                    for p in deception_proofs
                ],
                "count": len(deception_proofs),
            }
            deception_verdicts.append(deception_verdict)
            await self._emit(event_callback, ev(
                event_type=EventType.DECEPTION_LAB_VERDICT, actor=Actor.DTL, step=idx,
                source=rail_name.lower(), target="dtl",
                arrow_label=(f"DECEPTION LAB: {len(deception_proofs)} DETECTION(S)" if deception_proofs
                             else "DECEPTION LAB: CLEAN"),
                severity="critical" if deception_proofs else "success",
                payload=deception_verdict,
            ), pause=0.35)

            # ---- global DTL aggregation
            dtl_status = "UNPROTECTED_RAIL_BLIND"
            proof = None
            violations: List[Any] = []
            containment_action = "NONE"
            projected = exposure_before + tx.amount

            if dtl_enabled:
                await self._emit(event_callback, ev(
                    event_type=EventType.DTL_EVALUATION, actor=Actor.DTL, step=idx,
                    source="dtl", target="dtl",
                    arrow_label=f"CHECKING ALL 6 AUTHORITY DIMENSIONS: Rs {projected:,.0f} / Rs {auth.global_budget_ceiling:,.0f}",
                    severity="warning",
                    payload={
                        "exposure_before": round(exposure_before, 2),
                        "transaction_amount": tx.amount,
                        "projected_exposure": round(projected, 2),
                        "ceiling": auth.global_budget_ceiling,
                        "headroom_before": round(headroom_before, 2),
                        "utilization_pct": round(projected / auth.global_budget_ceiling * 100.0, 2)
                        if auth.global_budget_ceiling > 0 else 0.0,
                        "invariant_expression": "settled + authorized + pending + reserved + new_tx <= ceiling",
                        # The whole grant is re-checked, not only the money.
                        "dimensions_checked": [row["dimension"] for row in self.invariant_engine.registry()],
                        "rail_in_scope": auth.allows_rail(tx.rail),
                        "mcc_in_scope": tx.merchant_mcc in auth.permitted_mccs,
                        "per_transaction_cap": auth.per_transaction_cap,
                        "authority_expired": auth.is_expired(),
                    },
                ), pause=0.55)

                # Every dimension is evaluated, not just the first failure, so a
                # transaction that breaks the grant in two ways reports both.
                violations = self.invariant_engine.evaluate_all(auth, tx)
                proof = violations[0] if violations else None
                is_valid = not violations

                if is_valid:
                    dtl_status = "INVARIANTS_SATISFIED"
                    self.ledger.finalize_authorized_spend(auth.authority_id, tx.amount)
                else:
                    dtl_status = "CONTAINED_BY_DTL"
                    last_proof = proof
                    await self._emit(event_callback, ev(
                        event_type=EventType.INVARIANT_VIOLATION, actor=Actor.DTL, step=idx,
                        source="dtl", target="cost_governor",
                        arrow_label=f"VIOLATION: {proof.invariant_code}",
                        severity="critical",
                        payload={
                            "invariant_code": proof.invariant_code,
                            "authority_dimension": proof.authority_dimension,
                            "all_violated_invariants": [
                                {"code": p.invariant_code, "dimension": p.authority_dimension,
                                 "severity": p.severity}
                                for p in violations
                            ],
                            "severity": proof.severity,
                            "explanation": proof.explanation,
                            "invariant_expression": proof.invariant_expression,
                            "authorized_intent": proof.authorized_intent_summary,
                            "actual_cart": proof.actual_cart_summary,
                            "drift_score": proof.drift_score,
                            "violated_skus": proof.violated_skus,
                            "cumulative_before": proof.cumulative_spend_before,
                            "attempted_amount": proof.attempted_amount,
                            "ceiling": proof.global_ceiling,
                            "exposure_after": proof.total_exposure_after,
                            "overshoot": round(max(0.0, proof.total_exposure_after - proof.global_ceiling), 2),
                            "proof_id": proof.proof_id,
                        },
                    ), pause=0.7)

                # ---- Intent Firewall: the same proofs, reshaped into a
                # per-dimension drift vector and an ALLOW/PARTIAL_DRIFT/
                # HARD_DRIFT verdict. Emitted every step, not only on a
                # violation, so "nothing drifted" is as visible as a breach.
                verdict = firewall_decision.evaluate(tx.tx_id, violations)
                firewall_verdicts.append(verdict)
                await self._emit(event_callback, ev(
                    event_type=EventType.INTENT_FIREWALL_VERDICT, actor=Actor.DTL, step=idx,
                    source="dtl", target="cost_governor",
                    arrow_label=f"INTENT FIREWALL: {verdict['verdict']}",
                    severity="critical" if verdict["verdict"] == "HARD_DRIFT"
                    else "warning" if verdict["verdict"] == "PARTIAL_DRIFT" else "success",
                    payload=verdict,
                ), pause=0.3)
            else:
                # DTL disabled: this is the "legacy world" comparison.
                self.ledger.finalize_authorized_spend(auth.authority_id, tx.amount)

            await self._emit(event_callback, ev(
                event_type=EventType.DTL_EXPOSURE_UPDATED, actor=Actor.DTL, step=idx,
                source="dtl", target="exposure_meter",
                arrow_label=f"GLOBAL EXPOSURE Rs {auth.total_exposure_global:,.0f} / Rs {auth.global_budget_ceiling:,.0f}",
                severity="warning" if auth.total_exposure_global > auth.global_budget_ceiling else "info",
                payload={
                    "exposure_before": round(exposure_before, 2),
                    "exposure_after": round(auth.total_exposure_global, 2),
                    "amount_applied": round(auth.total_exposure_global - exposure_before, 2),
                    "ceiling": auth.global_budget_ceiling,
                    "headroom_after": round(auth.authority_headroom, 2),
                    "utilization_pct": round(
                        auth.total_exposure_global / auth.global_budget_ceiling * 100.0, 2)
                    if auth.global_budget_ceiling > 0 else 0.0,
                    "breached": auth.total_exposure_global > auth.global_budget_ceiling,
                },
            ), pause=0.4)

            # ---- ML scoring (real trained model)
            prob, is_anom, explanation = self.detector.evaluate_transaction(auth, tx, explain=True)
            model_loaded = self.detector.model_loaded
            if model_loaded and prob == prob:  # NaN check
                highest_ml_prob = max(highest_ml_prob, prob)

            await self._emit(event_callback, ev(
                event_type=EventType.ML_SCORE, actor=Actor.ML_DETECTOR, step=idx,
                source="dtl", target="ml_detector",
                arrow_label=(f"ML RISK {prob*100:.1f}%" if model_loaded else "MODEL NOT TRAINED"),
                severity="critical" if (model_loaded and is_anom) else "info",
                payload={
                    "model_loaded": model_loaded,
                    "probability": None if not model_loaded else round(float(prob), 4),
                    "is_anomaly": bool(is_anom),
                    "threshold": 0.5,
                    "backend": self.detector.backend_name,
                    "status": "SCORED" if model_loaded else "MODEL NOT TRAINED - run `python -m app.detector.train`",
                    "tx_id": tx.tx_id,
                },
            ), pause=0.45)

            if explanation and explanation.get("contributions"):
                await self._emit(event_callback, ev(
                    event_type=EventType.SHAP_EXPLANATION, actor=Actor.ML_DETECTOR, step=idx,
                    source="ml_detector", target="explanation_panel",
                    arrow_label=f"TOP DRIVER: {(explanation.get('top_features') or [['n/a', 0]])[0][0]}",
                    payload={
                        "method": explanation.get("method"),
                        "is_genuine_shap": explanation.get("is_genuine_shap", False),
                        "base_value": explanation.get("base_value"),
                        "top_features": explanation.get("top_features", [])[:8],
                        "note": explanation.get("note"),
                        "tx_id": tx.tx_id,
                    },
                ), pause=0.4)

            # ---- graceful containment
            if dtl_enabled and proof is not None:
                contained_tx, containment_action = self.cost_governor.apply_containment(auth, tx, proof)

                await self._emit(event_callback, ev(
                    event_type=EventType.POLICY_DECISION, actor=Actor.COST_GOVERNOR, step=idx,
                    source="cost_governor", target="outcome",
                    arrow_label="CONTAINING WITHOUT LOCKING THE USER OUT",
                    severity="success",
                    payload={
                        "action": containment_action,
                        "invariant_code": proof.invariant_code,
                        "policy": _policy_name(auth.active_policy),
                        "tx_state": _policy_name(contained_tx.state),
                        "rationale": proof.explanation,
                    },
                ), pause=0.5)

                if "PARTIAL_AUTH" in containment_action or "HEADROOM_CAP" in containment_action:
                    await self._emit(event_callback, ev(
                        event_type=EventType.PARTIAL_AUTH, actor=Actor.COST_GOVERNOR, step=idx,
                        source="cost_governor", target="outcome",
                        arrow_label="PARTIAL AUTHORIZATION - GENUINE ITEMS CLEARED",
                        severity="success",
                        payload={"detail": containment_action, "tx_id": tx.tx_id},
                    ), pause=0.4)
                else:
                    await self._emit(event_callback, ev(
                        event_type=EventType.QUARANTINE, actor=Actor.COST_GOVERNOR, step=idx,
                        source="cost_governor", target="quarantine",
                        arrow_label="QUARANTINED TO SHADOW SANDBOX",
                        severity="success",
                        payload={"detail": containment_action, "tx_id": tx.tx_id},
                    ), pause=0.4)

                if auth.active_policy == DefensePolicy.CAPABILITY_QUARANTINED:
                    await self._emit(event_callback, ev(
                        event_type=EventType.CAPABILITY_REDUCTION, actor=Actor.COST_GOVERNOR, step=idx,
                        source="cost_governor", target="red_agent",
                        arrow_label="AGENT CAPABILITY REDUCED",
                        severity="success",
                        payload={"new_policy": _policy_name(auth.active_policy),
                                 "new_ceiling": round(auth.global_budget_ceiling, 2)},
                    ), pause=0.35)

                # Containment can book a partial amount, so republish exposure.
                await self._emit(event_callback, ev(
                    event_type=EventType.DTL_EXPOSURE_UPDATED, actor=Actor.DTL, step=idx,
                    source="cost_governor", target="exposure_meter",
                    arrow_label=f"POST-CONTAINMENT EXPOSURE Rs {auth.total_exposure_global:,.0f} / Rs {auth.global_budget_ceiling:,.0f}",
                    severity="success",
                    payload={
                        "exposure_after": round(auth.total_exposure_global, 2),
                        "ceiling": auth.global_budget_ceiling,
                        "headroom_after": round(auth.authority_headroom, 2),
                        "utilization_pct": round(
                            auth.total_exposure_global / auth.global_budget_ceiling * 100.0, 2)
                        if auth.global_budget_ceiling > 0 else 0.0,
                        "after_containment": True,
                    },
                ), pause=0.35)

            step_results.append({
                "step": idx,
                "tx": tx.model_dump(),
                "local_rail_verdict": local_verdict,
                "dtl_defense_status": dtl_status,
                "ml_probability": None if not model_loaded else round(float(prob), 4),
                "ml_model_loaded": model_loaded,
                "explanation": explanation,
                "proof": proof.model_dump(mode="json") if proof else None,
                "containment": containment_action,
                "exposure_before": round(exposure_before, 2),
                "exposure_after": round(auth.total_exposure_global, 2),
                "headroom_after": round(auth.authority_headroom, 2),
            })

        # ---- cryptographic audit of the resulting DTL state
        # Sign the actual head of the event hash chain. Signing a placeholder
        # would produce a signature that commits to no history at all.
        pqc_audit = self.pqc_module.create_signed_snapshot(
            auth.model_dump(), event_root=self.recorder.log_root()
        )
        await self._emit(event_callback, ev(
            event_type=EventType.PQC_SIGN, actor=Actor.PQC_AUDITOR,
            source="dtl", target="pqc_auditor",
            arrow_label=("SIGNING DTL SNAPSHOT (ML-DSA-44)" if pqc_audit["pqc_available"]
                         else "PQC MODULE UNAVAILABLE"),
            severity="info",
            payload={
                "algorithm": pqc_audit["signature_algorithm"],
                "backend": pqc_audit.get("signature_backend"),
                "canonical_state_hash": pqc_audit["canonical_state_hash"],
                "event_log_root": self.recorder.log_root(),
                "signature_preview": (pqc_audit["signature_hex"][:48] + "...") if pqc_audit.get("signature_hex") else None,
                "signature_bytes": pqc_audit.get("signature_bytes_len"),
                "public_key_fingerprint": pqc_audit.get("public_key_fingerprint"),
                "available": pqc_audit["pqc_available"],
            },
        ), pause=0.45)

        tamper = self.pqc_module.run_tamper_test(auth.model_dump())
        await self._emit(event_callback, ev(
            event_type=EventType.PQC_VERIFY, actor=Actor.PQC_AUDITOR,
            source="pqc_auditor", target="audit_panel",
            arrow_label=(pqc_audit["verification_status"]),
            severity="success" if pqc_audit["is_cryptographically_valid"] else "warning",
            payload={"verification_status": pqc_audit["verification_status"], "tamper_tests": tamper},
        ), pause=0.4)

        # ---- closed loop: red observes the defence and picks its next move
        detected = last_proof is not None
        blue_outcome = self.feedback_engine.record_round_outcome(
            round_id=round_number,
            strategy=strategy,
            target_rails=[self._rail_name(t) for t in attacks],
            attempted_amount=total_objective,
            is_detected=detected,
            detection_score=highest_ml_prob,
            violating_invariant=last_proof.invariant_code if last_proof else None,
            defense_action="CONTAINED" if detected else "ALLOW",
            red_reasoning=f"Executed {strategy}",
            auth_state=auth,
        )
        next_plan = self.feedback_engine.plan_next_strategy()

        await self._emit(event_callback, ev(
            event_type=EventType.RED_ADAPTATION, actor=Actor.RED_AGENT,
            source="outcome", target="red_agent",
            arrow_label=(f"NEXT STRATEGY: {next_plan.get('next_strategy', 'n/a').replace('_', ' ')}"),
            severity="critical",
            payload=next_plan,
        ), pause=0.4)

        if detected:
            policy_changes = blue_outcome.get("policy_changes", {})
            await self._emit(event_callback, ev(
                event_type=EventType.BLUE_ADAPTATION, actor=Actor.DTL,
                source="dtl", target="policy",
                arrow_label=(f"POLICY ESCALATED: {_policy_name(auth.active_policy)}"
                             if policy_changes.get("escalated")
                             else f"POLICY HARDENED: {_policy_name(auth.active_policy)}"),
                severity="success",
                payload={
                    "active_policy": _policy_name(auth.active_policy),
                    "violating_invariant": last_proof.invariant_code if last_proof else None,
                    "violation_count": policy_changes.get("violation_count", 1),
                    "escalated": policy_changes.get("escalated", False),
                    "blue_adaptation_summary": blue_outcome.get("blue_adaptation_summary", ""),
                },
            ), pause=0.35)

        # Three genuinely different endings, and conflating them misrepresents
        # the result. An agent that spends its whole grant without violating any
        # dimension has NOT beaten the defence - it acted inside its authority,
        # which is the system working. Only an unchecked breach is a red win.
        breached = auth.total_exposure_global > auth.global_budget_ceiling
        if detected:
            winner, outcome = "BLUE", "CONTAINED"
            label, severity = "ATTACK CONTAINED", "success"
        elif not dtl_enabled:
            winner, outcome = "RED", "UNCHECKED_BREACH"
            label, severity = "ATTACK SUCCEEDED - NO GLOBAL CHECK", "critical"
        else:
            winner, outcome = "NONE", "WITHIN_AUTHORITY"
            label, severity = "NO VIOLATION - SPEND STAYED INSIDE THE GRANT", "info"

        await self._emit(event_callback, ev(
            event_type=EventType.ATTACK_COMPLETE, actor=Actor.SYSTEM,
            arrow_label=label,
            severity=severity,
            payload={
                "strategy": strategy,
                "detected": detected,
                "dtl_enabled": dtl_enabled,
                "steps": len(step_results),
                "final_exposure": round(auth.total_exposure_global, 2),
                "ceiling": auth.global_budget_ceiling,
                "breached": breached,
                "winner": winner,
                "outcome": outcome,
            },
        ), pause=0.2)

        self.is_running = False
        self.last_firewall_verdicts = firewall_verdicts
        self.last_deception_verdicts = deception_verdicts

        round_summary = {
            "round_number": round_number,
            "strategy": strategy,
            "dtl_enabled": dtl_enabled,
            "experiment_id": exp_id,
            "step_results": step_results,
            "firewall_verdicts": firewall_verdicts,
            "deception_verdicts": deception_verdicts,
            "authority_state": self.get_state()["authority_state"],
            "pqc_audit": pqc_audit,
            "pqc_tamper_tests": tamper,
            "winner": winner,
            "outcome": outcome,
            "detected": detected,
            "next_red_plan": next_plan,
            "adaptation_history": self.feedback_engine.get_adaptation_history(),
            "events": self.recorder.timeline(),
        }
        round_summary["kill_chain"] = score_kill_chain_round(round_summary)
        round_summary["risk"] = compute_unified_risk(round_summary)
        self.round_history.append(round_summary["kill_chain"])
        self.last_round = round_summary
        return round_summary

    # ----------------------------------------------------------- campaign

    # Deliberately NOT the master-prompt narrative verbatim (a scripted
    # "Round 3 blocked by Graph Sentinel" moment) - graph features are a
    # training-time signal (see graph_sentinel/), not something the live
    # single-authority arena evaluates per-transaction, and claiming
    # otherwise here would misrepresent what this system actually does live.
    # This default instead runs RAIL_SCOPE_VIOLATION three times back to
    # back, which demonstrates something the live arena genuinely does: the
    # full Blue escalation ladder (soft response -> capability quarantine ->
    # mandate suspension) that Module 5 added, end to end in one call.
    DEFAULT_CAMPAIGN: List[int] = [7, 7, 7]

    async def run_campaign(
        self,
        round_numbers: Optional[List[int]] = None,
        dtl_enabled: bool = True,
        event_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
        speed: float = 1.0,
    ) -> Dict[str, Any]:
        """
        Runs a sequence of rounds back to back on THIS orchestrator (so
        escalation state and round_history accumulate across them), and
        returns each round's summary plus the session-level kill-chain
        coverage rollup over just this campaign's rounds.
        """
        sequence = list(round_numbers) if round_numbers else list(self.DEFAULT_CAMPAIGN)
        rounds: List[Dict[str, Any]] = []
        for round_number in sequence:
            result = await self.run_round_stream(
                round_number=round_number, dtl_enabled=dtl_enabled,
                event_callback=event_callback, speed=speed,
            )
            rounds.append(result)

        return {
            "round_numbers": sequence,
            "rounds": rounds,
            "kill_chain_coverage": kill_chain_coverage(
                [r["kill_chain"] for r in rounds]
            ),
            "final_active_policy": _policy_name(
                self.ledger.get_authority(AUTHORITY_ID).active_policy
            ),
        }
