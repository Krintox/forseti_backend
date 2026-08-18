"""
HTTP surface for the AI agent layer.

Every route returns the standard agent envelope, so the frontend can always tell
whether it is looking at model output, a deterministic fallback, or an honest
"unavailable". None of these routes can change an authorization outcome.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from ..paths import ABLATION_PATH, BASELINES_PATH, METRICS_PATH
from . import agents as A
from .llm_client import get_client

router = APIRouter(prefix="/api/ai", tags=["ai"])


def _load(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


class IntentRequest(BaseModel):
    instruction: str


class CartRequest(BaseModel):
    intent_summary: str = "Household groceries within the delegated budget"
    items: List[Dict[str, Any]]
    merchant_mcc: str = "5411"
    merchant_name: str = "Fresh Direct Mart"


class ExplainRequest(BaseModel):
    event: Dict[str, Any]
    context: Optional[Dict[str, Any]] = None


class MerchantRequest(BaseModel):
    name: str
    description: str
    declared_mcc: str = "5411"


class RegulatoryRequest(BaseModel):
    control: str
    description: str


class QueryRequest(BaseModel):
    question: str


class CounterfactualRequest(BaseModel):
    question: str = "What delegated limit would have stopped this attack entirely?"


@router.get("/status")
def ai_status() -> Dict[str, Any]:
    """Provider health plus the catalog of what each agent is for."""
    return {
        "llm": get_client().status(),
        "agents": A.AGENT_CATALOG,
        "agent_count": len(A.AGENT_CATALOG),
        # Served alongside the catalog so the UI can never present twelve agents
        # as twelve independent inventions. The DTL is the invention; these are
        # the intelligence layer around it.
        "hierarchy": A.SYSTEM_HIERARCHY,
        "design_rule": "The LLM never decides an authorization outcome. It explains, "
                       "translates and proposes; every proposal is schema-validated and "
                       "re-checked by the deterministic engine.",
    }


@router.post("/intent/compile")
def compile_intent(req: IntentRequest) -> Dict[str, Any]:
    return A.compile_intent(req.instruction)


@router.post("/cart/audit")
def audit_cart(req: CartRequest) -> Dict[str, Any]:
    return A.audit_cart(req.intent_summary, req.items, req.merchant_mcc, req.merchant_name)


@router.post("/event/explain")
def explain_event(req: ExplainRequest) -> Dict[str, Any]:
    return A.explain_event(req.event, req.context)


@router.post("/merchant/profile")
def profile_merchant(req: MerchantRequest) -> Dict[str, Any]:
    return A.profile_merchant(req.name, req.description, req.declared_mcc)


@router.post("/regulatory/map")
def map_regulations(req: RegulatoryRequest) -> Dict[str, Any]:
    return A.map_regulations(req.control, req.description)


@router.get("/model-card")
def model_card() -> Dict[str, Any]:
    metrics = _load(METRICS_PATH)
    if not metrics:
        return {"agent": "model_card", "status": "NO_ARTIFACT",
                "result": None,
                "reason": "No metrics.json. Run `python -m app.detector.train` first."}
    return A.generate_model_card(metrics, _load(BASELINES_PATH), _load(ABLATION_PATH))


def register(app, orchestrator) -> None:
    """
    Wires the routes that need live arena state. Kept as a closure so the AI
    layer never imports the orchestrator module directly.
    """

    @router.post("/red/propose")
    def propose_attack() -> Dict[str, Any]:
        auth = orchestrator.ledger.get_authority("auth_household_grocery_2026")
        history = orchestrator.feedback_engine.get_adaptation_history()
        observed = [{"strategy": h.get("strategy"), "contained": h.get("is_detected"),
                     "detector_score": h.get("detection_score"),
                     "invariant": h.get("violating_invariant")} for h in history]
        return A.propose_attack(observed, auth.global_budget_ceiling, auth.authority_headroom)

    @router.post("/incident/report")
    def incident_report() -> Dict[str, Any]:
        if orchestrator.last_round is None:
            return {"agent": "incident_report", "status": "NO_INCIDENT", "result": None,
                    "reason": "No round has been executed yet. Run an attack first."}
        return A.write_incident_report(orchestrator.last_round, orchestrator.recorder.timeline())

    @router.post("/policy/advise")
    def advise_policy() -> Dict[str, Any]:
        last = orchestrator.last_round
        if last is None:
            return {"agent": "policy_advisor", "status": "NO_INCIDENT", "result": None,
                    "reason": "No round has been executed yet."}
        violation = next((s.get("proof") for s in last.get("step_results", []) if s.get("proof")), None)
        if not violation:
            return {"agent": "policy_advisor", "status": "NO_VIOLATION", "result": None,
                    "reason": "The last round produced no invariant violation to advise on."}
        auth = orchestrator.ledger.get_authority("auth_household_grocery_2026")
        return A.advise_policy(violation, auth.model_dump(mode="json"),
                               orchestrator.feedback_engine.get_adaptation_history())

    @router.post("/customer/notice")
    def customer_notice() -> Dict[str, Any]:
        last = orchestrator.last_round
        if last is None:
            return {"agent": "customer_notice", "status": "NO_INCIDENT", "result": None,
                    "reason": "No round has been executed yet."}
        step = next((s for s in last.get("step_results", []) if s.get("proof")), None)
        if not step:
            return {"agent": "customer_notice", "status": "NO_VIOLATION", "result": None,
                    "reason": "Nothing was held, so there is no customer to notify."}
        auth = orchestrator.ledger.get_authority("auth_household_grocery_2026")
        return A.write_customer_notice(step.get("containment", ""), auth.model_dump(mode="json"),
                                       step.get("proof") or {})

    @router.post("/log/query")
    def log_query(req: QueryRequest) -> Dict[str, Any]:
        compiled = A.compile_log_query(req.question)
        events = orchestrator.recorder.timeline()
        if compiled.get("status") != "OK" or not compiled.get("result"):
            compiled["matched_events"] = []
            compiled["match_count"] = 0
            compiled["searched"] = len(events)
            return compiled
        filtered = A.apply_log_filter(events, compiled["result"])
        matched = filtered["events"]
        compiled["text_term_dropped"] = filtered["text_term_dropped"]
        if filtered["text_term_dropped"]:
            compiled["result"]["explanation"] += (
                f" (the literal term \"{filtered['text_term_dropped']}\" matched nothing, "
                f"so only the structural filter was applied)")
        compiled["match_count"] = len(matched)
        compiled["searched"] = len(events)
        # Trim payloads so a big log cannot blow up the response.
        compiled["matched_events"] = [
            {"sequence": e.get("sequence"), "event_type": e.get("event_type"),
             "actor": e.get("actor"), "arrow_label": e.get("arrow_label"),
             "severity": e.get("severity"), "offset_ms": e.get("offset_ms")}
            for e in matched[:60]
        ]
        compiled["executed_by"] = "deterministic filter over the real event log"
        return compiled

    @router.post("/counterfactual")
    async def counterfactual(req: CounterfactualRequest) -> Dict[str, Any]:
        """
        The model proposes ceilings; the SIMULATOR answers. Each proposal is
        re-run for real against the same attack, and the outcome reported is the
        engine's, not the model's.
        """
        last = orchestrator.last_round
        if last is None:
            return {"agent": "counterfactual_analyst", "status": "NO_INCIDENT", "result": None,
                    "reason": "No round has been executed yet."}

        proposal = A.propose_counterfactual(req.question, last)
        if proposal.get("status") != "OK" or not proposal.get("result"):
            return proposal

        strategy = last.get("strategy", "CROSS_RAIL_SPLIT")
        original_ceiling = orchestrator.configured_ceiling
        runs = []
        try:
            for param in proposal["result"]["parameters_to_test"][:4]:
                ceiling = float(param["ceiling_inr"])
                orchestrator.reset(ceiling)
                result = await orchestrator.run_round_stream(
                    round_number=2, dtl_enabled=True, event_callback=None,
                    speed=100.0, strategy_override=strategy)
                runs.append({
                    "label": param.get("label", ""),
                    "ceiling_inr": ceiling,
                    "contained": result["detected"],
                    "winner": result["winner"],
                    "final_exposure_inr": result["authority_state"]["total_exposure_global"],
                    "breached": result["authority_state"]["total_exposure_global"] > ceiling,
                })
        finally:
            orchestrator.reset(original_ceiling)

        proposal["simulated_outcomes"] = runs
        proposal["answer"] = _counterfactual_answer(runs)
        proposal["method"] = ("The model proposed the ceilings; the deterministic simulator "
                              "re-ran the identical attack against each and produced these outcomes.")
        return proposal

    app.include_router(router)


def _counterfactual_answer(runs: List[Dict[str, Any]]) -> str:
    """States the result from the simulation runs, never from the model."""
    if not runs:
        return "No scenarios were simulated."
    contained = [r for r in runs if r["contained"]]
    if not contained:
        return (f"None of the {len(runs)} tested ceilings contained the attack. "
                f"Lower ceilings than those proposed would be required.")
    best = max(contained, key=lambda r: r["ceiling_inr"])
    return (f"{len(contained)} of {len(runs)} tested ceilings contained the attack. "
            f"The highest ceiling that still contained it was "
            f"INR {best['ceiling_inr']:,.0f} ({best['label'] or 'proposed'}).")
