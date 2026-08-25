"""
FORSETI AI agents.

Twelve agents, each aimed at a problem that appears the moment agentic payments
ship for real. They share three rules:

  1. THE LLM NEVER ENFORCES. It explains, translates and proposes. Every
     proposal is schema-validated and re-checked by the deterministic engine
     before it can affect an outcome.
  2. EVERY AGENT DEGRADES HONESTLY. No key, no quota, bad JSON -> the envelope
     says LLM_UNAVAILABLE or FALLBACK and names the reason. Nothing is invented.
  3. NOTHING IS HARDCODED. Inputs come from live DTL state, real event logs and
     generated artifacts; outputs are computed per call.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from .llm_client import LLMUnavailable, Reply, get_client

# Merchant categories the simulator understands. Used to constrain LLM output
# so a hallucinated MCC can never reach the policy engine.
KNOWN_MCCS: Dict[str, str] = {
    "5411": "Grocery stores & supermarkets",
    "5499": "Miscellaneous food stores",
    "5311": "Department stores",
    "5812": "Eating places & restaurants",
    "5541": "Service stations",
    "4900": "Utilities",
    "5912": "Drug stores & pharmacies",
    "5732": "Electronics stores",
    "5734": "Computer software stores",
    "5045": "Computers & peripherals",
    "5944": "Jewellery stores",
    "7995": "Betting & gambling",
    "6051": "Quasi-cash / crypto",
}

SEMANTIC_EXCLUSION_VOCAB = [
    "GIFT_CARD", "STORED_VALUE", "CRYPTO", "PREPAID_VOUCHER",
    "CASH_EQUIVALENT", "GAMBLING", "MONEY_TRANSFER", "JEWELLERY",
]

RAILS = ["CARD_TOKEN", "UPI_CIRCLE", "AGENTIC_AP2"]


def _envelope(agent: str, status: str, result: Any, reply: Optional[Reply] = None,
              **extra: Any) -> Dict[str, Any]:
    env: Dict[str, Any] = {
        "agent": agent,
        "status": status,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "result": result,
        "llm": None,
        "enforcement": "advisory only - the deterministic engine decides",
    }
    if reply is not None:
        env["llm"] = {
            "provider": reply.provider,
            "model": reply.model,
            "latency_ms": reply.latency_ms,
            "cached": reply.cached,
        }
    env.update(extra)
    return env


def _unavailable(agent: str, exc: Exception, fallback: Any = None) -> Dict[str, Any]:
    return _envelope(
        agent,
        "FALLBACK" if fallback is not None else "LLM_UNAVAILABLE",
        fallback,
        reason=str(exc),
        note=("Deterministic fallback shown; no model output was available."
              if fallback is not None else
              "No language model answered. Nothing is substituted."),
    )


def _run(agent: str, system: str, user: str, schema: str, *,
         validate: Callable[[Dict[str, Any]], Dict[str, Any]],
         fallback: Optional[Callable[[], Any]] = None,
         max_tokens: int = 900, cache_key: Optional[str] = None) -> Dict[str, Any]:
    """Shared path: call, parse, validate, envelope. Never raises to the API."""
    try:
        data, reply = get_client().chat_json(system, user, schema_hint=schema,
                                             max_tokens=max_tokens, cache_key=cache_key)
    except LLMUnavailable as exc:
        return _unavailable(agent, exc, fallback() if fallback else None)
    except ValueError as exc:  # unparseable JSON
        return _unavailable(agent, exc, fallback() if fallback else None)

    try:
        validated = validate(data)
    except Exception as exc:  # noqa: BLE001 - a bad proposal must not crash the API
        return _envelope(agent, "REJECTED", fallback() if fallback else None, reply,
                         reason=f"model output failed validation: {exc}",
                         raw_model_output=data)
    return _envelope(agent, "OK", validated, reply)


# =====================================================================
# 1. INTENT COMPILER
# =====================================================================

INTENT_SYSTEM = """You compile a human's natural-language spending instruction into a
machine-checkable delegation policy for an AI payment agent.

You are a translator, not an approver. Be conservative: when the human is vague,
choose the NARROWER reading. Anything you allow, an autonomous agent may spend.

Only use merchant category codes from the provided list. Only use exclusion tags
from the provided vocabulary.

CURRENCY: every amount is Indian Rupees. Write it as INR or the rupee sign. Never use dollars, pounds or euros."""


def compile_intent(instruction: str) -> Dict[str, Any]:
    """
    Turns "buy groceries, up to 10k, nothing re-sellable" into an enforceable
    policy object.

    Real problem: a delegation is only as good as its machine-readable form.
    Today a human's intent is flattened into a spend limit and an MCC allowlist,
    and everything the human actually meant is lost at that boundary.
    """
    schema = json.dumps({
        "ceiling_inr": "number",
        "permitted_mccs": ["string (from the provided list only)"],
        "semantic_exclusions": ["string (from the provided vocabulary only)"],
        "permitted_rails": ["CARD_TOKEN|UPI_CIRCLE|AGENTIC_AP2"],
        "window_hours": "number",
        "per_transaction_cap_inr": "number",
        "economic_purpose": "one short sentence",
        "ambiguities": ["anything the human left unclear, and how you resolved it"],
        "confidence": "0.0-1.0",
    }, indent=2)

    user = (f"Human instruction:\n\"{instruction}\"\n\n"
            f"Allowed merchant category codes:\n{json.dumps(KNOWN_MCCS, indent=2)}\n\n"
            f"Allowed exclusion tags: {SEMANTIC_EXCLUSION_VOCAB}\n"
            f"Allowed rails: {RAILS}")

    def validate(d: Dict[str, Any]) -> Dict[str, Any]:
        ceiling = float(d.get("ceiling_inr") or 0)
        if ceiling <= 0:
            raise ValueError("ceiling_inr must be positive")
        # Reject any MCC or tag the engine does not know: a hallucinated code
        # would silently widen the policy.
        mccs = [str(m) for m in (d.get("permitted_mccs") or []) if str(m) in KNOWN_MCCS]
        if not mccs:
            raise ValueError("no recognised merchant category codes")
        tags = [t for t in (d.get("semantic_exclusions") or []) if t in SEMANTIC_EXCLUSION_VOCAB]
        rails = [r for r in (d.get("permitted_rails") or RAILS) if r in RAILS] or RAILS
        cap = float(d.get("per_transaction_cap_inr") or ceiling)
        return {
            "ceiling_inr": round(ceiling, 2),
            "per_transaction_cap_inr": round(min(cap, ceiling), 2),
            "permitted_mccs": mccs,
            "permitted_mcc_labels": {m: KNOWN_MCCS[m] for m in mccs},
            "semantic_exclusions": tags,
            "permitted_rails": rails,
            "window_hours": float(d.get("window_hours") or 24),
            "economic_purpose": str(d.get("economic_purpose") or "")[:240],
            "ambiguities": [str(a)[:200] for a in (d.get("ambiguities") or [])][:6],
            "confidence": max(0.0, min(1.0, float(d.get("confidence") or 0.5))),
            "dropped_by_validator": {
                "unknown_mccs": [str(m) for m in (d.get("permitted_mccs") or []) if str(m) not in KNOWN_MCCS],
                "unknown_tags": [t for t in (d.get("semantic_exclusions") or []) if t not in SEMANTIC_EXCLUSION_VOCAB],
            },
        }

    return _run("intent_compiler", INTENT_SYSTEM, user, schema, validate=validate,
                cache_key=f"intent:{hashlib.sha256(instruction.encode()).hexdigest()[:16]}")


# =====================================================================
# 2. SEMANTIC CART AUDITOR
# =====================================================================

CART_SYSTEM = """You audit a shopping cart against the economic purpose a human authorised.

The merchant category code may be perfectly valid while the cart subverts the
purpose - a supermarket that sells gift cards is the classic case. Judge the
ECONOMIC SUBSTANCE: does this basket consume value for the stated purpose, or
does it convert the budget into something re-sellable, transferable or liquid?

Score 0.0 (fully within purpose) to 1.0 (entirely outside it).

CURRENCY: every amount is Indian Rupees. Write it as INR or the rupee sign. Never use dollars, pounds or euros."""


def audit_cart(intent_summary: str, items: List[Dict[str, Any]], mcc: str,
               merchant: str) -> Dict[str, Any]:
    """
    Real problem: MCC is far too coarse to express intent. This is the gap that
    makes intent laundering work, and no rail checks it today.
    """
    schema = json.dumps({
        "drift_score": "0.0-1.0",
        "verdict": "WITHIN_PURPOSE|PARTIAL_DRIFT|OUTSIDE_PURPOSE",
        "offending_items": [{"name": "string", "why": "string",
                             "economic_type": "CONSUMABLE|DURABLE|STORED_VALUE|LIQUID|SERVICE"}],
        "legitimate_value_inr": "number",
        "suspicious_value_inr": "number",
        "reasoning": "2-3 sentences",
    }, indent=2)

    total = sum(float(i.get("unit_price", 0)) * int(i.get("quantity", 1)) for i in items)
    user = (f"Authorised purpose: {intent_summary}\n"
            f"Merchant: {merchant} (MCC {mcc} - {KNOWN_MCCS.get(mcc, 'unknown')})\n"
            f"Cart total: INR {total:,.2f}\n"
            f"Items:\n{json.dumps(items, indent=2, default=str)}")

    def validate(d: Dict[str, Any]) -> Dict[str, Any]:
        score = max(0.0, min(1.0, float(d.get("drift_score", 0))))
        verdict = str(d.get("verdict", "")).upper()
        if verdict not in ("WITHIN_PURPOSE", "PARTIAL_DRIFT", "OUTSIDE_PURPOSE"):
            verdict = "OUTSIDE_PURPOSE" if score > 0.66 else ("PARTIAL_DRIFT" if score > 0.25 else "WITHIN_PURPOSE")
        legit = float(d.get("legitimate_value_inr") or 0)
        susp = float(d.get("suspicious_value_inr") or 0)
        # The split must reconcile with the real cart total, not the model's arithmetic.
        if legit + susp > 0:
            scale = total / (legit + susp)
            legit, susp = legit * scale, susp * scale
        return {
            "drift_score": round(score, 3),
            "verdict": verdict,
            "offending_items": (d.get("offending_items") or [])[:8],
            "legitimate_value_inr": round(legit, 2),
            "suspicious_value_inr": round(susp, 2),
            "cart_total_inr": round(total, 2),
            "reasoning": str(d.get("reasoning") or "")[:600],
        }

    return _run("cart_auditor", CART_SYSTEM, user, schema, validate=validate, max_tokens=700)


# =====================================================================
# 3. EVENT EXPLAINER  (drives the clickable log)
# =====================================================================

EVENT_SYSTEM = """You explain one step of a live payment-security simulation to a person
watching a dashboard.

Answer four things, plainly and specifically, using the numbers you are given:
  WHAT happened, HOW it was done, WHY the actor did it, and WHY IT MATTERS.

Write for an intelligent non-specialist. No hedging, no filler, no restating the
label. If the actor is the attacker, explain the attacker's incentive; if it is a
defence component, explain what it protects and what it costs.

CURRENCY: every amount is Indian Rupees. Write it as INR or the rupee sign. Never use dollars, pounds or euros."""


def explain_event(event: Dict[str, Any], context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Real problem: security dashboards show *that* something fired, never *why*.
    Analysts lose hours reconstructing intent from raw logs.
    """
    payload = event.get("payload") or {}
    schema = json.dumps({
        "headline": "one short sentence",
        "what_happened": "2-3 sentences",
        "how_it_was_done": "2-3 sentences, mechanism level",
        "why_the_actor_did_it": "2-3 sentences on motive",
        "why_it_matters": "2-3 sentences on consequence",
        "team": "RED|BLUE|NEUTRAL",
        "analogy": "one plain-language analogy a non-expert would understand",
    }, indent=2)

    user = (f"Event type: {event.get('event_type')}\n"
            f"Actor: {event.get('actor')}\n"
            f"Step: {event.get('step')}  Round: {event.get('round_id')}\n"
            f"On-screen label: {event.get('arrow_label')}\n"
            f"Severity: {event.get('severity')}\n"
            f"Payload:\n{json.dumps(payload, indent=2, default=str)[:1800]}\n")
    if context:
        user += f"\nSurrounding context:\n{json.dumps(context, indent=2, default=str)[:900]}"

    red_actors = {"RED_AGENT"}
    blue_actors = {"DTL", "ML_DETECTOR", "COST_GOVERNOR", "PQC_AUDITOR"}
    actor = str(event.get("actor", ""))
    default_team = "RED" if actor in red_actors else ("BLUE" if actor in blue_actors else "NEUTRAL")

    def validate(d: Dict[str, Any]) -> Dict[str, Any]:
        team = str(d.get("team", "")).upper()
        return {
            "headline": str(d.get("headline") or event.get("arrow_label") or "")[:200],
            "what_happened": str(d.get("what_happened") or "")[:800],
            "how_it_was_done": str(d.get("how_it_was_done") or "")[:800],
            "why_the_actor_did_it": str(d.get("why_the_actor_did_it") or "")[:800],
            "why_it_matters": str(d.get("why_it_matters") or "")[:800],
            "analogy": str(d.get("analogy") or "")[:400],
            "team": team if team in ("RED", "BLUE", "NEUTRAL") else default_team,
        }

    return _run("event_explainer", EVENT_SYSTEM, user, schema, validate=validate,
                fallback=lambda: deterministic_event_explanation(event),
                max_tokens=800,
                cache_key=f"evt:{event.get('event_type')}:{event.get('step')}:"
                          f"{hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:12]}")


# Written from the same event fields the LLM sees, so the panel is never empty
# and never wrong when the model is unreachable.
_EVENT_TEMPLATES: Dict[str, Dict[str, str]] = {
    "AUTHORITY_GRANTED": {
        "team": "NEUTRAL",
        "what": "The user's delegation was (re)stated in full: a ceiling of {ceiling}, plus rail, purpose, merchant and time scope.",
        "how": "The DTL recorded every dimension of the grant, not only the amount, so each can be checked independently.",
        "why": "A ceiling alone cannot express \"UPI only\" or \"this week only\" - those need their own fields.",
        "matters": "The attack that follows is scored against every dimension of this grant, not just the number.",
    },
    "ROUND_STARTED": {
        "team": "NEUTRAL",
        "what": "A new attack round began against a delegated authority of {ceiling}.",
        "how": "The orchestrator selected the {strategy} vector and reset the per-cycle rail counters.",
        "why": "Each round is one controlled experiment, so the same attack can be replayed deterministically.",
        "matters": "Everything after this point is measured against the {ceiling} the user actually granted.",
    },
    "ATTACK_STEP": {
        "team": "RED",
        "what": "The Red agent sent {amount} through the {rail} rail.",
        "how": "It built a transaction sized to sit comfortably inside that rail's own limit.",
        "why": "Staying under the local limit is the whole point: a rail that sees nothing unusual will approve.",
        "matters": "This leg is unremarkable alone. Only the running total across rails exposes it.",
    },
    "RAIL_APPROVED": {
        "team": "NEUTRAL",
        "what": "The {rail} rail approved {amount}.",
        "how": "It checked its own ceiling and merchant scope, both of which passed.",
        "why": "The rail is behaving correctly. It has no visibility into the other rails.",
        "matters": "A correct local decision can still be part of a global violation.",
    },
    "DTL_EVALUATION": {
        "team": "BLUE",
        "what": "The Delegation-Trust Ledger aggregated every rail: {projected} against a {ceiling} ceiling.",
        "how": "It summed settled, authorized, pending and reserved spend, then added this transaction.",
        "why": "Aggregate exposure is the only view in which cross-rail splitting is visible.",
        "matters": "This is the check no individual rail can perform.",
    },
    "INVARIANT_VIOLATION": {
        "team": "BLUE",
        "what": "Invariant {invariant} failed: exposure would reach {exposure_after} against {ceiling}.",
        "how": "A deterministic arithmetic check, not a model score, so it needs no training data.",
        "why": "The agent exceeded the authority the human granted, regardless of rail.",
        "matters": "This is arithmetic, so it holds for attack patterns never seen before.",
    },
    "ML_SCORE": {
        "team": "BLUE",
        "what": "The trained detector scored this transaction at {probability}.",
        "how": "Gradient-boosted trees over 37 features spanning transaction, delegation, cross-rail, semantic, security and graph groups.",
        "why": "The model catches behavioural and semantic patterns that arithmetic alone misses.",
        "matters": "A low score on a split leg is expected and honest: alone, it genuinely looks ordinary.",
    },
    "INTENT_FIREWALL_VERDICT": {
        "team": "BLUE",
        "what": "The Intent Firewall reshaped this transaction's invariant proofs into one {verdict} verdict.",
        "how": "Every proof the DTL already computed was mapped onto its authority dimension and combined into a single per-dimension drift score.",
        "why": "A judge reasons about how far an action drifted from the grant on every axis at once, not which invariant code fired.",
        "matters": "HARD_DRIFT means the action is outside the delegated authority in a way that must be blocked, not merely queried.",
    },
    "DECEPTION_LAB_VERDICT": {
        "team": "BLUE",
        "what": "The Deception Lab checked whether the agent itself was fed a false premise: {deception_verdict}.",
        "how": "Four deterministic detectors re-derive ground truth from data no deception can touch, independent of the authority checks.",
        "why": "An agent can be manipulated by a prompt injection, a poisoned tool result, a fabricated memory, or a self-issued escalation - none of which are authority violations by themselves.",
        "matters": "Detection here never changes the authorization outcome; none of these fields are read by anything that decides one.",
    },
    "BLUE_ADAPTATION": {
        "team": "BLUE",
        "what": "The defence hardened its policy to {active_policy} after this containment.",
        "how": "Response strength escalates with repetition: this is occurrence {violation_count} of this invariant this session.",
        "why": "Reacting identically to the fifth repeat of an attack already contained four times is not really adapting.",
        "matters": "A persistent attacker on the same dimension is met with an escalating response, not a fixed one.",
    },
    "POLICY_DECISION": {
        "team": "BLUE",
        "what": "The cost governor chose a containment action rather than a blanket block.",
        "how": "It separated legitimate basket value from the unauthorised remainder and authorised only the former.",
        "why": "Blocking everything is itself an attack: flood revocations and you lock out the real customer.",
        "matters": "Legitimate commerce continues while the unauthorised portion is isolated.",
    },
    "PQC_SIGN": {
        "team": "BLUE",
        "what": "The resulting ledger state was signed with NIST FIPS 204 ML-DSA-44.",
        "how": "The state was canonicalised, chained to the previous log entry, and signed with a lattice-based key.",
        "why": "An attacker who can edit the audit log can erase the evidence of the theft.",
        "matters": "Signatures harvested today stay unforgeable even against a future quantum attacker.",
    },
    "RED_ADAPTATION": {
        "team": "RED",
        "what": "The Red agent re-scored its options and selected a different strategy.",
        "how": "Each candidate is scored from observed containment rate and detector confidence, then the best is taken.",
        "why": "A contained strategy has no expected value left, so pressing it again would be irrational.",
        "matters": "Real adversaries adapt. A defence evaluated only against a fixed attack set is over-rated.",
    },
}


def deterministic_event_explanation(event: Dict[str, Any]) -> Dict[str, Any]:
    """Template explanation built from the event's own numbers. Never invented."""
    p = event.get("payload") or {}
    etype = str(event.get("event_type", ""))

    def money(v: Any) -> str:
        try:
            return f"INR {float(v):,.0f}"
        except (TypeError, ValueError):
            return "an unstated amount"

    fields = {
        "ceiling": money(p.get("ceiling") or p.get("delegated_ceiling")),
        "amount": money(p.get("amount") or p.get("transaction_amount")),
        "projected": money(p.get("projected_exposure") or p.get("exposure_after")),
        "exposure_after": money(p.get("exposure_after") or p.get("total_exposure_after")),
        "rail": str(p.get("rail", "a payment rail")).replace("_", " "),
        "strategy": str(p.get("strategy", "an attack")).replace("_", " "),
        "invariant": str(p.get("invariant_code", "an authority invariant")),
        "probability": (f"{float(p['probability']) * 100:.1f}%"
                        if isinstance(p.get("probability"), (int, float)) else "an unavailable score"),
        "verdict": str(p.get("verdict", "ALLOW")),
        "deception_verdict": ("no deception detected" if p.get("verdict") != "DECEPTION_DETECTED"
                              else f"{p.get('count', 0)} detection(s) found"),
        "active_policy": str(p.get("active_policy", "STANDARD")),
        "violation_count": str(p.get("violation_count", 1)),
    }

    # INVARIANT_VIOLATION can fire for any of the six authority dimensions
    # (amount, per-transaction, rail, merchant, purpose, time), each with a
    # genuinely different mechanism. Rather than force one budget-shaped
    # template onto all six, use the dimension-specific explanation the
    # invariant engine itself already produced in dtl/invariant_engine.py.
    if etype == "INVARIANT_VIOLATION":
        dimension = str(p.get("authority_dimension", "AMOUNT"))
        code = str(p.get("invariant_code", "an authority invariant"))
        explanation = str(p.get("explanation") or "")
        others = p.get("all_violated_invariants") or []
        also = ""
        if len(others) > 1:
            other_codes = ", ".join(o.get("code", "") for o in others[1:] if o.get("code"))
            also = f" It also violates {other_codes} at the same time."
        if not explanation:
            explanation = (f"Invariant {code} failed: exposure would reach {fields['exposure_after']} "
                           f"against a ceiling of {fields['ceiling']}.")
        return {
            "headline": event.get("arrow_label") or f"{code} violated",
            "what_happened": explanation + also,
            "how_it_was_done": (f"The DTL evaluated all six authority dimensions "
                                f"(amount, per-transaction, rail, merchant, purpose, time) "
                                f"against the grant; the {dimension} check failed."),
            "why_the_actor_did_it": ("The agent's action was legal on the rail it used and "
                                     "would have been legal in isolation - it only breaks the "
                                     "delegation the human actually granted."),
            "why_it_matters": ("Authority is multidimensional: staying under the money ceiling "
                               "does not authorise every rail, merchant, basket or moment."),
            "analogy": "",
            "team": "BLUE",
            "source": "deterministic_template",
        }

    tpl = _EVENT_TEMPLATES.get(etype)
    if not tpl:
        return {
            "headline": event.get("arrow_label") or etype,
            "what_happened": f"The {event.get('actor', 'system')} emitted {etype}.",
            "how_it_was_done": "See the payload for the exact values recorded at this step.",
            "why_the_actor_did_it": "This step is part of the standard round sequence.",
            "why_it_matters": "Every step is recorded in the hash-chained log and can be replayed.",
            "analogy": "",
            "team": "NEUTRAL",
            "source": "deterministic_template",
        }

    def fmt(s: str) -> str:
        try:
            return s.format(**fields)
        except KeyError:
            return s

    return {
        "headline": event.get("arrow_label") or etype,
        "what_happened": fmt(tpl["what"]),
        "how_it_was_done": fmt(tpl["how"]),
        "why_the_actor_did_it": fmt(tpl["why"]),
        "why_it_matters": fmt(tpl["matters"]),
        "analogy": "",
        "team": tpl["team"],
        "source": "deterministic_template",
    }


# =====================================================================
# 4. ADVERSARIAL RED STRATEGIST
# =====================================================================

RED_SYSTEM = """You are a payment-security red teamer working inside a sandboxed simulator.
Every rail, merchant and identity here is synthetic; no real money or credential exists.

Given what the defence has already caught, propose the next attack variant. Think
about what the defence measures and what it does NOT measure. Propose parameters
only - the simulator executes them, you do not.

CURRENCY: every amount is Indian Rupees. Write it as INR or the rupee sign. Never use dollars, pounds or euros."""


def propose_attack(observed: List[Dict[str, Any]], ceiling: float,
                   headroom: float) -> Dict[str, Any]:
    """
    Real problem: defences are graded against the attacks their authors imagined.
    This lets an adversary reason about the *observed* defence and generate a
    variant nobody wrote down. The simulator still executes it deterministically.
    """
    schema = json.dumps({
        "strategy": "CROSS_RAIL_SPLIT|INTENT_LAUNDERING|VELOCITY_BURST|SCOPE_CREEP|REVOCATION_FLOOD|BASELINE_POISONING",
        "leg_amounts_inr": ["number"],
        "rails": ["CARD_TOKEN|UPI_CIRCLE|AGENTIC_AP2"],
        "merchant_mcc": "string from the known list",
        "hypothesis": "what defensive weakness this is probing",
        "why_this_evades": "2-3 sentences",
        "expected_defence": "which invariant you expect to fire, if any",
        "confidence": "0.0-1.0",
    }, indent=2)

    user = (f"Delegated ceiling: INR {ceiling:,.2f}\n"
            f"Remaining headroom: INR {headroom:,.2f}\n"
            f"Known merchant categories: {json.dumps(KNOWN_MCCS)}\n"
            f"Rails available: {RAILS}\n\n"
            f"Previously observed outcomes:\n{json.dumps(observed[-8:], indent=2, default=str)[:2000]}")

    valid_strategies = {"CROSS_RAIL_SPLIT", "INTENT_LAUNDERING", "VELOCITY_BURST",
                        "SCOPE_CREEP", "REVOCATION_FLOOD", "BASELINE_POISONING"}

    def validate(d: Dict[str, Any]) -> Dict[str, Any]:
        strategy = str(d.get("strategy", "")).upper()
        if strategy not in valid_strategies:
            raise ValueError(f"unknown strategy {strategy!r}")
        legs = [float(x) for x in (d.get("leg_amounts_inr") or []) if float(x) > 0][:6]
        if not legs:
            raise ValueError("no positive leg amounts proposed")
        # Hard sandbox bound: a proposal can never exceed 5x the grant.
        cap = ceiling * 5
        if sum(legs) > cap:
            scale = cap / sum(legs)
            legs = [round(x * scale, 2) for x in legs]
        rails = [r for r in (d.get("rails") or []) if r in RAILS] or RAILS[:len(legs)]
        mcc = str(d.get("merchant_mcc", "5411"))
        if mcc not in KNOWN_MCCS:
            mcc = "5411"
        return {
            "strategy": strategy,
            "leg_amounts_inr": [round(x, 2) for x in legs],
            "total_inr": round(sum(legs), 2),
            "rails": (rails * len(legs))[:len(legs)],
            "merchant_mcc": mcc,
            "merchant_category": KNOWN_MCCS[mcc],
            "hypothesis": str(d.get("hypothesis") or "")[:400],
            "why_this_evades": str(d.get("why_this_evades") or "")[:600],
            "expected_defence": str(d.get("expected_defence") or "")[:200],
            "confidence": max(0.0, min(1.0, float(d.get("confidence") or 0.5))),
            "sandbox_note": "Parameters only. The deterministic simulator executes and judges this.",
        }

    return _run("red_strategist", RED_SYSTEM, user, schema, validate=validate, max_tokens=800)


# =====================================================================
# 5. INCIDENT REPORT WRITER
# =====================================================================

INCIDENT_SYSTEM = """You write the incident report a payments compliance team must file after
an automated control contains a suspicious delegated-agent transaction.

Be factual and specific. Use only the figures supplied. Where evidence is absent,
write "not established" rather than speculating. This document may be read by a
regulator.

CURRENCY: every amount is Indian Rupees. Write it as INR or the rupee sign. Never use dollars, pounds or euros."""


def write_incident_report(round_result: Dict[str, Any], events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Real problem: every contained incident generates a reporting obligation.
    Analysts write these by hand from raw logs; it is hours of work per incident.
    """
    schema = json.dumps({
        "title": "string",
        "severity": "LOW|MEDIUM|HIGH|CRITICAL",
        "executive_summary": "3-4 sentences for a non-technical reader",
        "timeline": [{"time": "offset", "event": "string"}],
        "root_cause": "2-3 sentences",
        "financial_exposure_inr": "number",
        "controls_that_fired": ["string"],
        "controls_that_did_not_fire": ["string"],
        "customer_impact": "2 sentences",
        "recommended_actions": ["string"],
        "evidence_integrity": "1-2 sentences on how the log is tamper-evident",
    }, indent=2)

    steps = round_result.get("step_results", [])
    key_events = [{"offset_s": round((e.get("offset_ms") or 0) / 1000, 2),
                   "type": e.get("event_type"), "label": e.get("arrow_label")}
                  for e in events
                  if e.get("event_type") in ("ATTACK_STEP", "RAIL_APPROVED", "INVARIANT_VIOLATION",
                                             "INTENT_FIREWALL_VERDICT", "DECEPTION_LAB_VERDICT",
                                             "POLICY_DECISION", "PARTIAL_AUTH", "QUARANTINE",
                                             "BLUE_ADAPTATION", "PQC_SIGN")]

    appendix = _incident_deterministic_appendix(round_result)

    user = (f"Strategy: {round_result.get('strategy')}\n"
            f"Contained: {round_result.get('detected')}   Winner: {round_result.get('winner')}\n"
            f"Delegated ceiling: INR {round_result.get('authority_state', {}).get('global_budget_ceiling', 0):,.2f}\n"
            f"Final exposure: INR {round_result.get('authority_state', {}).get('total_exposure_global', 0):,.2f}\n"
            f"Transactions: {len(steps)}\n"
            f"Kill-chain stage: {appendix['kill_chain_stage']} ({appendix['kill_chain_stage_code']})\n"
            f"Intent Firewall hard-drift detections: {appendix['intent_firewall_hard_drift_count']} "
            f"(dimensions: {appendix['intent_firewall_violating_dimensions']})\n"
            f"Deception Lab detections: {appendix['deception_lab_detection_count']} "
            f"(types: {appendix['deception_lab_types']})\n"
            f"Per-step outcomes:\n{json.dumps([{k: s.get(k) for k in ('step', 'local_rail_verdict', 'dtl_defense_status', 'ml_probability', 'containment')} for s in steps], indent=2, default=str)[:1500]}\n"
            f"Event timeline:\n{json.dumps(key_events, indent=2)[:1500]}")

    def validate(d: Dict[str, Any]) -> Dict[str, Any]:
        sev = str(d.get("severity", "")).upper()
        return {
            "title": str(d.get("title") or "Delegated-agent containment incident")[:200],
            "severity": sev if sev in ("LOW", "MEDIUM", "HIGH", "CRITICAL") else "HIGH",
            "executive_summary": str(d.get("executive_summary") or "")[:1200],
            "timeline": (d.get("timeline") or [])[:20],
            "root_cause": str(d.get("root_cause") or "")[:800],
            "financial_exposure_inr": float(d.get("financial_exposure_inr") or 0),
            "controls_that_fired": [str(x)[:160] for x in (d.get("controls_that_fired") or [])][:10],
            "controls_that_did_not_fire": [str(x)[:160] for x in (d.get("controls_that_did_not_fire") or [])][:10],
            "customer_impact": str(d.get("customer_impact") or "")[:600],
            "recommended_actions": [str(x)[:200] for x in (d.get("recommended_actions") or [])][:10],
            "evidence_integrity": str(d.get("evidence_integrity") or "")[:400],
        }

    env = _run("incident_report", INCIDENT_SYSTEM, user, schema, validate=validate, max_tokens=1600)
    # Present regardless of whether the LLM answered - an incident report's
    # FACTS must never depend on LLM availability, only its narrative prose
    # does. Sourced directly from the round result, never from the model.
    env["deterministic_appendix"] = appendix
    return env


def _incident_deterministic_appendix(round_result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Cross-module facts the narrative sections above can reference, computed
    directly from what Modules 1-3 already recorded for this round - not
    re-derived, not asked of the model.
    """
    kill_chain = round_result.get("kill_chain") or {}
    stage = kill_chain.get("stage") or {}
    firewall = round_result.get("firewall_verdicts") or []
    deception = round_result.get("deception_verdicts") or []

    hard_drift = [v for v in firewall if v.get("verdict") == "HARD_DRIFT"]
    detected_deception = [v for v in deception if v.get("verdict") == "DECEPTION_DETECTED"]

    return {
        "kill_chain_stage": stage.get("label"),
        "kill_chain_stage_code": stage.get("code"),
        "time_to_detection_ms": kill_chain.get("time_to_detection_ms"),
        "economic_exposure_prevented_inr": kill_chain.get("economic_exposure_prevented_inr"),
        "blast_radius_score": kill_chain.get("blast_radius_score"),
        "attack_chain_score": kill_chain.get("attack_chain_score"),
        "intent_firewall_hard_drift_count": len(hard_drift),
        "intent_firewall_violating_dimensions": sorted({
            dim for v in hard_drift for dim in v.get("violating_dimensions", [])
        }),
        "deception_lab_detection_count": len(detected_deception),
        "deception_lab_types": sorted({
            det["type"] for v in detected_deception for det in v.get("detections", [])
        }),
    }


# =====================================================================
# 6. BLUE POLICY ADVISOR
# =====================================================================

POLICY_SYSTEM = """You advise the defence team after a containment event.

Propose the SMALLEST policy change that would have prevented this without
harming legitimate spending. Over-tightening is a real cost: it blocks genuine
customers and pushes them to less-safe channels. Quantify the trade-off.

CURRENCY: every amount is Indian Rupees. Write it as INR or the rupee sign. Never use dollars, pounds or euros."""


def advise_policy(violation: Dict[str, Any], authority: Dict[str, Any],
                  history: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Real problem: after an incident, "tighten everything" is the reflex, and it
    quietly destroys conversion. This forces the trade-off to be stated.
    """
    schema = json.dumps({
        "recommended_changes": [{
            "parameter": "ceiling_inr|per_transaction_cap_inr|window_hours|semantic_exclusions|permitted_mccs|step_up_threshold",
            "from": "current value",
            "to": "proposed value",
            "rationale": "string",
            "expected_false_positive_impact": "NONE|LOW|MEDIUM|HIGH",
        }],
        "would_have_prevented": "true|false",
        "legitimate_traffic_risk": "2 sentences on what this change costs genuine customers",
        "alternative_to_blocking": "string",
        "confidence": "0.0-1.0",
    }, indent=2)

    user = (f"Violation:\n{json.dumps(violation, indent=2, default=str)[:1200]}\n\n"
            f"Current authority:\n{json.dumps({k: authority.get(k) for k in ('global_budget_ceiling', 'permitted_mccs', 'semantic_exclusions', 'active_policy')}, indent=2, default=str)}\n\n"
            f"Recent rounds:\n{json.dumps(history[-5:], indent=2, default=str)[:1000]}")

    allowed_params = {"ceiling_inr", "per_transaction_cap_inr", "window_hours",
                      "semantic_exclusions", "permitted_mccs", "step_up_threshold"}

    def validate(d: Dict[str, Any]) -> Dict[str, Any]:
        changes = []
        for c in (d.get("recommended_changes") or [])[:6]:
            param = str(c.get("parameter", ""))
            if param not in allowed_params:
                continue  # a change to a parameter the engine has no knob for is dropped
            changes.append({
                "parameter": param,
                "from": c.get("from"),
                "to": c.get("to"),
                "rationale": str(c.get("rationale") or "")[:400],
                "expected_false_positive_impact": str(c.get("expected_false_positive_impact", "MEDIUM")).upper(),
            })
        if not changes:
            raise ValueError("no actionable parameter changes proposed")
        return {
            "recommended_changes": changes,
            "would_have_prevented": bool(d.get("would_have_prevented")),
            "legitimate_traffic_risk": str(d.get("legitimate_traffic_risk") or "")[:600],
            "alternative_to_blocking": str(d.get("alternative_to_blocking") or "")[:400],
            "confidence": max(0.0, min(1.0, float(d.get("confidence") or 0.5))),
            "applied": False,
            "note": "Advisory. Nothing is applied to the live policy without an operator action.",
        }

    return _run("policy_advisor", POLICY_SYSTEM, user, schema, validate=validate, max_tokens=1100)


# =====================================================================
# 7. CUSTOMER NOTICE WRITER
# =====================================================================

CUSTOMER_SYSTEM = """You write the message a bank sends a customer whose AI assistant just had
a purchase held.

The customer is not technical and is probably annoyed. Be clear about what was
held, what still went through, why, and exactly what they can do next. Never
blame the customer. Never use jargon. Under 130 words.

CURRENCY: every amount is Indian Rupees. Write it as INR or the rupee sign. Never use dollars, pounds or euros."""


def write_customer_notice(containment: str, authority: Dict[str, Any],
                          violation: Dict[str, Any]) -> Dict[str, Any]:
    """
    Real problem: when an agent's purchase is held, the customer gets a generic
    decline. That destroys trust in agentic payments faster than the fraud does.
    """
    schema = json.dumps({
        "sms": "under 160 characters",
        "app_notification": {"title": "string", "body": "string"},
        "email_subject": "string",
        "email_body": "under 130 words, plain language",
        "next_steps": ["string"],
        "tone_check": "confirm this does not blame the customer",
    }, indent=2)

    user = (f"Containment action: {containment}\n"
            f"Authorised budget: INR {authority.get('global_budget_ceiling', 0):,.2f}\n"
            f"Reason: {violation.get('explanation', 'aggregate authority exceeded')}\n"
            f"Invariant: {violation.get('invariant_code', 'n/a')}")

    def validate(d: Dict[str, Any]) -> Dict[str, Any]:
        notif = d.get("app_notification") or {}
        return {
            "sms": str(d.get("sms") or "")[:200],
            "app_notification": {"title": str(notif.get("title") or "")[:80],
                                 "body": str(notif.get("body") or "")[:300]},
            "email_subject": str(d.get("email_subject") or "")[:160],
            "email_body": str(d.get("email_body") or "")[:1400],
            "next_steps": [str(s)[:160] for s in (d.get("next_steps") or [])][:6],
            "tone_check": str(d.get("tone_check") or "")[:200],
        }

    return _run("customer_notice", CUSTOMER_SYSTEM, user, schema, validate=validate, max_tokens=800)


# =====================================================================
# 8. REGULATORY MAPPER
# =====================================================================

REG_SYSTEM = """You map an automated payment-security control to the regulatory obligations it
helps evidence, for an Indian payments context.

Only cite frameworks you are confident exist (RBI, NPCI, PCI DSS, FIU-IND, NIST,
BIS). State the obligation in your own words. If you are unsure of a clause
number, omit the number rather than guessing it. Mark relevance honestly."""


def map_regulations(control: str, description: str) -> Dict[str, Any]:
    """
    Real problem: a novel control is worthless commercially until a bank can map
    it to an obligation it already has to satisfy.
    """
    schema = json.dumps({
        "mappings": [{
            "framework": "string",
            "obligation": "string in your own words",
            "how_this_control_helps": "string",
            "relevance": "DIRECT|SUPPORTING|TANGENTIAL",
            "clause_reference": "string or null if unsure",
        }],
        "gaps": ["obligations this control does NOT satisfy"],
        "caveat": "string",
    }, indent=2)

    user = f"Control: {control}\nWhat it does: {description}"

    def validate(d: Dict[str, Any]) -> Dict[str, Any]:
        maps = []
        for m in (d.get("mappings") or [])[:10]:
            rel = str(m.get("relevance", "")).upper()
            maps.append({
                "framework": str(m.get("framework") or "")[:120],
                "obligation": str(m.get("obligation") or "")[:400],
                "how_this_control_helps": str(m.get("how_this_control_helps") or "")[:400],
                "relevance": rel if rel in ("DIRECT", "SUPPORTING", "TANGENTIAL") else "SUPPORTING",
                "clause_reference": (str(m["clause_reference"])[:80]
                                     if m.get("clause_reference") else None),
            })
        if not maps:
            raise ValueError("no mappings produced")
        return {
            "mappings": maps,
            "gaps": [str(g)[:200] for g in (d.get("gaps") or [])][:8],
            "caveat": str(d.get("caveat") or "")[:400],
            "verification_note": "Model-generated mapping. Confirm every citation with counsel "
                                 "before relying on it; clause numbers are especially error-prone.",
        }

    return _run("regulatory_mapper", REG_SYSTEM, user, schema, validate=validate, max_tokens=1300,
                cache_key=f"reg:{hashlib.sha256(control.encode()).hexdigest()[:12]}")


# =====================================================================
# 9. MERCHANT RISK PROFILER
# =====================================================================

MERCHANT_SYSTEM = """You assess whether a merchant's declared category matches what it actually
sells, from its name and description.

Category mis-declaration is a real laundering route: a business registered under
groceries that mostly sells gift cards gives an attacker a compliant-looking
route to liquid value. Judge the mismatch, not the merchant's honesty.

CURRENCY: every amount is Indian Rupees. Write it as INR or the rupee sign. Never use dollars, pounds or euros."""


def profile_merchant(name: str, description: str, declared_mcc: str) -> Dict[str, Any]:
    """
    Real problem: MCC is self-declared and rarely re-checked. Attackers shop for
    merchants whose declared category is cleaner than their actual inventory.
    """
    schema = json.dumps({
        "declared_category": "string",
        "inferred_category": "string",
        "mismatch": "true|false",
        "stored_value_exposure": "NONE|LOW|MEDIUM|HIGH",
        "risk_score": "0.0-1.0",
        "reasoning": "2-3 sentences",
        "suggested_mcc": "string from the known list",
        "recommended_action": "ALLOW|MONITOR|REVIEW|RESTRICT",
    }, indent=2)

    user = (f"Merchant: {name}\nDescription: {description}\n"
            f"Declared MCC: {declared_mcc} ({KNOWN_MCCS.get(declared_mcc, 'unknown')})\n"
            f"Known categories: {json.dumps(KNOWN_MCCS)}")

    def validate(d: Dict[str, Any]) -> Dict[str, Any]:
        action = str(d.get("recommended_action", "")).upper()
        suggested = str(d.get("suggested_mcc", declared_mcc))
        return {
            "declared_category": KNOWN_MCCS.get(declared_mcc, "unknown"),
            "inferred_category": str(d.get("inferred_category") or "")[:160],
            "mismatch": bool(d.get("mismatch")),
            "stored_value_exposure": str(d.get("stored_value_exposure", "LOW")).upper(),
            "risk_score": max(0.0, min(1.0, float(d.get("risk_score") or 0))),
            "reasoning": str(d.get("reasoning") or "")[:600],
            "suggested_mcc": suggested if suggested in KNOWN_MCCS else declared_mcc,
            "recommended_action": action if action in ("ALLOW", "MONITOR", "REVIEW", "RESTRICT") else "MONITOR",
        }

    return _run("merchant_profiler", MERCHANT_SYSTEM, user, schema, validate=validate, max_tokens=700)


# =====================================================================
# 10. COUNTERFACTUAL ANALYST
# =====================================================================

COUNTERFACTUAL_SYSTEM = """You answer "what would have happened if..." about a payment-security
incident.

Propose concrete parameter values to test. You do NOT decide the answer - a
deterministic simulator re-runs the scenario with your parameters and reports the
real outcome. Propose values worth testing, and say what you expect.

CURRENCY: every amount is Indian Rupees. Write it as INR or the rupee sign. Never use dollars, pounds or euros."""


def propose_counterfactual(
    question: str,
    round_result: Dict[str, Any],
    available_dimensions: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Real problem: "would a lower limit have stopped this?" is the first question
    every risk committee asks, and it is normally answered by intuition.

    `available_dimensions` restricts what the model may propose to what the
    caller can actually replay honestly. Some vectors (RAIL_SCOPE_VIOLATION,
    PER_TX_BREACH, LAPSED_MANDATE, BENEFICIARY_DRIFT, CONSTRAINT_EROSION) run
    against a FIXED authority profile that IS the demonstration - a proposed
    "what if the card rail had been disabled" for one of those would be
    silently overwritten by that fixed profile at replay time and the answer
    would misrepresent what was actually tested. Those rounds only ever offer
    ["AMOUNT"]; everything else also offers RAIL and PURPOSE.
    """
    dims = available_dimensions or ["AMOUNT"]
    schema_shape: List[Dict[str, str]] = []
    if "AMOUNT" in dims:
        schema_shape.append({"dimension": "AMOUNT", "ceiling_inr": "number", "label": "string"})
    if "RAIL" in dims:
        schema_shape.append({"dimension": "RAIL",
                              "disable_rail": "CARD_TOKEN | UPI_CIRCLE | AGENTIC_AP2", "label": "string"})
    if "PURPOSE" in dims:
        schema_shape.append({"dimension": "PURPOSE", "permit_gift_cards": "boolean", "label": "string"})
    schema = json.dumps({
        "parameters_to_test": schema_shape,
        "hypothesis": "string",
        "what_to_watch": "string",
    }, indent=2)

    auth = round_result.get("authority_state", {})
    user = (f"Question: {question}\n"
            f"Actual ceiling: INR {auth.get('global_budget_ceiling', 0):,.2f}\n"
            f"Actual final exposure: INR {auth.get('total_exposure_global', 0):,.2f}\n"
            f"Strategy: {round_result.get('strategy')}  Contained: {round_result.get('detected')}\n"
            f"Legs: {json.dumps([s.get('tx', {}).get('amount') for s in round_result.get('step_results', [])])}\n"
            f"Dimensions you may propose parameters for THIS round: {dims}")

    def validate(d: Dict[str, Any]) -> Dict[str, Any]:
        params = []
        for p in (d.get("parameters_to_test") or [])[:5]:
            dimension = str(p.get("dimension") or "AMOUNT").upper()
            if dimension not in dims:
                continue
            label = str(p.get("label") or "")[:80]
            if dimension == "AMOUNT":
                try:
                    ceiling = float(p.get("ceiling_inr"))
                except (TypeError, ValueError):
                    continue
                if ceiling > 0:
                    params.append({"dimension": "AMOUNT", "ceiling_inr": round(ceiling, 2), "label": label})
            elif dimension == "RAIL":
                rail = str(p.get("disable_rail") or "").upper()
                if rail in ("CARD_TOKEN", "UPI_CIRCLE", "AGENTIC_AP2"):
                    params.append({"dimension": "RAIL", "disable_rail": rail, "label": label})
            elif dimension == "PURPOSE":
                params.append({
                    "dimension": "PURPOSE",
                    "permit_gift_cards": bool(p.get("permit_gift_cards")),
                    "label": label,
                })
        if not params:
            raise ValueError("no testable parameters proposed within the allowed dimensions")
        return {
            "parameters_to_test": params,
            "hypothesis": str(d.get("hypothesis") or "")[:500],
            "what_to_watch": str(d.get("what_to_watch") or "")[:300],
            "note": "The simulator now re-runs each of these deterministically. "
                    "The model's expectation is not the answer.",
        }

    return _run("counterfactual_analyst", COUNTERFACTUAL_SYSTEM, user, schema,
                validate=validate, max_tokens=700)


# =====================================================================
# 11. LOG COPILOT  (natural language -> deterministic filter)
# =====================================================================

COPILOT_SYSTEM = """You translate a plain-English question about a payment-security event log
into a structured filter.

You never answer the question yourself - you only produce the filter that the
system then applies to the real log. Available event types are supplied."""

EVENT_TYPES = ["ROUND_STARTED", "ATTACK_STARTED", "ATTACK_STEP", "RAIL_REQUEST",
               "RAIL_APPROVED", "RAIL_DECLINED", "DTL_EVALUATION", "DTL_EXPOSURE_UPDATED",
               "INVARIANT_VIOLATION", "ML_SCORE", "SHAP_EXPLANATION", "POLICY_DECISION",
               "PARTIAL_AUTH", "QUARANTINE", "CAPABILITY_REDUCTION", "PQC_SIGN",
               "PQC_VERIFY", "RED_ADAPTATION", "BLUE_ADAPTATION", "ATTACK_COMPLETE",
               "ROUND_COMPLETE"]


def compile_log_query(question: str) -> Dict[str, Any]:
    """
    Real problem: analysts cannot write log queries at 2am during an incident.
    The LLM writes the filter; the deterministic engine runs it, so the answer is
    always computed from the real log rather than recalled by a model.
    """
    schema = json.dumps({
        "event_types": ["string from the supplied list"],
        "actors": ["RED_AGENT|CARD_RAIL|UPI_RAIL|AGENTIC_RAIL|DTL|ML_DETECTOR|COST_GOVERNOR|PQC_AUDITOR|SYSTEM"],
        "min_amount_inr": "number or null",
        "severity": ["info|success|warning|critical"],
        "text_contains": "string or null",
        "explanation": "one sentence describing what this filter selects",
    }, indent=2)

    user = f"Question: {question}\n\nAvailable event types:\n{json.dumps(EVENT_TYPES)}"

    def validate(d: Dict[str, Any]) -> Dict[str, Any]:
        types = [t for t in (d.get("event_types") or []) if t in EVENT_TYPES]
        sev = [s for s in (d.get("severity") or []) if s in ("info", "success", "warning", "critical")]
        min_amt = d.get("min_amount_inr")
        try:
            min_amt = float(min_amt) if min_amt not in (None, "", "null") else None
        except (TypeError, ValueError):
            min_amt = None
        return {
            "event_types": types,
            "actors": [str(a) for a in (d.get("actors") or [])][:8],
            "min_amount_inr": min_amt,
            "severity": sev,
            "text_contains": (str(d["text_contains"])[:80] if d.get("text_contains") else None),
            "explanation": str(d.get("explanation") or "")[:300],
        }

    return _run("log_copilot", COPILOT_SYSTEM, user, schema, validate=validate, max_tokens=500)


def apply_log_filter(events: List[Dict[str, Any]], f: Dict[str, Any]) -> Dict[str, Any]:
    """
    Runs the compiled filter over the real log. Pure and deterministic.

    `text_contains` is applied as a SOFT narrowing. Models like to add a literal
    term ("objection") that never appears verbatim in the log, which silently
    zeroes an otherwise correct structural filter. If the term eliminates every
    match we keep the structural result and say the term was dropped.
    """
    structural = _structural_filter(events, f)
    term = f.get("text_contains")
    if not term:
        return {"events": structural, "text_term_applied": None, "text_term_dropped": None}

    narrowed = [e for e in structural if term.lower() in json.dumps(e, default=str).lower()]
    if narrowed:
        return {"events": narrowed, "text_term_applied": term, "text_term_dropped": None}
    return {"events": structural, "text_term_applied": None, "text_term_dropped": term}


def _structural_filter(events: List[Dict[str, Any]], f: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for e in events:
        if f.get("event_types") and e.get("event_type") not in f["event_types"]:
            continue
        if f.get("actors") and e.get("actor") not in f["actors"]:
            continue
        if f.get("severity") and e.get("severity") not in f["severity"]:
            continue
        if f.get("min_amount_inr") is not None:
            p = e.get("payload") or {}
            amt = p.get("amount") or p.get("transaction_amount") or p.get("exposure_after") or 0
            try:
                if float(amt) < float(f["min_amount_inr"]):
                    continue
            except (TypeError, ValueError):
                continue
        out.append(e)
    return out


# =====================================================================
# 12. MODEL CARD GENERATOR
# =====================================================================

MODELCARD_SYSTEM = """You write an honest model card from real evaluation artifacts.

State limitations prominently. If a metric is weak, say so plainly - a model card
that hides weakness is worse than none. Use only the supplied numbers."""


def generate_model_card(metrics: Dict[str, Any], baselines: Dict[str, Any],
                        ablation: Dict[str, Any]) -> Dict[str, Any]:
    """
    Real problem: model documentation is a regulatory expectation and is always
    the last thing written. Generating it from artifacts keeps it truthful.
    """
    schema = json.dumps({
        "intended_use": "string",
        "out_of_scope_uses": ["string"],
        "training_data": "string",
        "evaluation_summary": "string",
        "known_weaknesses": ["string"],
        "fairness_considerations": "string",
        "monitoring_recommendations": ["string"],
        "headline_caveat": "the single most important limitation",
    }, indent=2)

    user = (f"Model: {metrics.get('model', {}).get('architecture')}\n"
            f"Test metrics: {json.dumps(metrics.get('test_metrics', {}), default=str)[:900]}\n"
            f"Attack-family holdout: {json.dumps(metrics.get('attack_family_holdout', {}), default=str)[:900]}\n"
            f"Calibration: {json.dumps(metrics.get('calibration', {}), default=str)[:300]}\n"
            f"Measured DTL lift: {json.dumps(ablation.get('measured_dtl_feature_lift', {}), default=str)[:300]}\n"
            f"Cross-rail recall by architecture: "
            f"{json.dumps(baselines.get('headline_finding', {}).get('cross_rail_split_recall_when_family_held_out', {}), default=str)[:500]}")

    def validate(d: Dict[str, Any]) -> Dict[str, Any]:
        weaknesses = [str(w)[:300] for w in (d.get("known_weaknesses") or [])][:10]
        if not weaknesses:
            raise ValueError("a model card with no stated weaknesses is not acceptable")
        return {
            "intended_use": str(d.get("intended_use") or "")[:800],
            "out_of_scope_uses": [str(x)[:200] for x in (d.get("out_of_scope_uses") or [])][:8],
            "training_data": str(d.get("training_data") or "")[:800],
            "evaluation_summary": str(d.get("evaluation_summary") or "")[:1000],
            "known_weaknesses": weaknesses,
            "fairness_considerations": str(d.get("fairness_considerations") or "")[:600],
            "monitoring_recommendations": [str(x)[:200] for x in (d.get("monitoring_recommendations") or [])][:8],
            "headline_caveat": str(d.get("headline_caveat") or "")[:400],
        }

    return _run("model_card", MODELCARD_SYSTEM, user, schema, validate=validate, max_tokens=1600)


AGENT_CATALOG = [
    {"id": "intent_compiler", "name": "Intent Compiler",
     "problem": "A delegation is only as strong as its machine-readable form. Human intent is flattened into a limit plus an MCC allowlist, and the meaning is lost at that boundary.",
     "solves": "Compiles natural-language authority into an enforceable policy object, and names every ambiguity it had to resolve."},
    {"id": "cart_auditor", "name": "Semantic Cart Auditor",
     "problem": "Merchant category codes are far too coarse to encode intent. A supermarket that also sells gift cards is a compliant-looking route to liquid value.",
     "solves": "Judges the economic substance of a basket against the authorised purpose and splits legitimate from suspicious value."},
    {"id": "event_explainer", "name": "Event Explainer",
     "problem": "Security dashboards show that a control fired, never why. Analysts reconstruct intent from raw logs by hand.",
     "solves": "Explains any step as what/how/why/why-it-matters, for both Red and Blue, with a deterministic template fallback."},
    {"id": "red_strategist", "name": "Adversarial Strategist",
     "problem": "Defences are graded against the attacks their authors imagined, which systematically over-rates them.",
     "solves": "Reasons about the observed defence and proposes novel attack parameters; the deterministic simulator executes and judges them."},
    {"id": "incident_report", "name": "Incident Report Writer",
     "problem": "Every contained incident creates a reporting obligation, written by hand from raw logs.",
     "solves": "Drafts a regulator-ready report from the actual event timeline, marking absent evidence as not established."},
    {"id": "policy_advisor", "name": "Policy Advisor",
     "problem": "After an incident the reflex is to tighten everything, quietly destroying legitimate conversion.",
     "solves": "Proposes the smallest sufficient change and states its false-positive cost. Nothing applies without an operator."},
    {"id": "customer_notice", "name": "Customer Notice Writer",
     "problem": "A held agent purchase reaches the customer as a generic decline, destroying trust faster than the fraud would.",
     "solves": "Writes SMS, in-app and email copy explaining what was held, what cleared, and what to do next."},
    {"id": "regulatory_mapper", "name": "Regulatory Mapper",
     "problem": "A novel control has no commercial value until a bank can map it to an obligation it already carries.",
     "solves": "Maps a control to RBI / NPCI / PCI / FIU obligations, omitting clause numbers it is unsure of and listing gaps."},
    {"id": "merchant_profiler", "name": "Merchant Risk Profiler",
     "problem": "MCC is self-declared and rarely re-checked, so attackers shop for merchants whose category is cleaner than their inventory.",
     "solves": "Infers actual category from name and description, flags mismatch and stored-value exposure."},
    {"id": "counterfactual_analyst", "name": "Counterfactual Analyst",
     "problem": "\"Would a lower limit have stopped this?\" is the first question a risk committee asks, and is normally answered by intuition.",
     "solves": "Proposes ceilings to test; the simulator re-runs the attack against each and reports the real outcome."},
    {"id": "log_copilot", "name": "Log Copilot",
     "problem": "Analysts cannot write log queries during a live incident.",
     "solves": "Compiles a plain-English question into a structured filter that the engine runs over the real log."},
    {"id": "model_card", "name": "Model Card Generator",
     "problem": "Model documentation is a regulatory expectation and always the last thing written.",
     "solves": "Generates an honest model card from real artifacts, and refuses to produce one with no stated weaknesses."},
]


# =====================================================================
# SYSTEM HIERARCHY
# =====================================================================
#
# Stated explicitly because the honest framing matters more than the agent
# count: the INVENTION is the delegation-authority engine. The twelve agents
# above are an intelligence layer wrapped around it - they make the core idea
# usable and broader, but not one of them decides an authorization outcome.
#
# Presenting twelve agents as twelve independent security innovations would
# misrepresent the architecture. This structure is what the UI renders instead.

SYSTEM_HIERARCHY = {
    "invention": {
        "name": "Delegation-Trust Ledger (DTL)",
        "claim": "A delegated agent may act only within the authority a human granted, "
                 "and that authority is multidimensional - amount, per-transaction size, "
                 "rail, merchant scope, beneficiary, economic purpose and validity window.",
        "why_novel": "Payment rails each enforce their own local limits. No rail can see the "
                     "others, and none of them holds the non-monetary dimensions of the grant "
                     "at all. The DTL is the only component that evaluates the whole grant.",
        "deterministic": True,
    },
    "core": [
        {"layer": "ATTACK", "component": "Red Agent",
         "role": "Generates adversarial transactions targeting one authority dimension at a time.",
         "module": "app/redteam/"},
        {"layer": "DEFENSE", "component": "DTL Invariant Engine",
         "role": "Seven deterministic invariants, one per authority dimension. No ML, no training data.",
         "module": "app/dtl/invariant_engine.py"},
        {"layer": "DEFENSE", "component": "Cost Governor",
         "role": "Proportionate containment chosen by which dimension failed - never a blanket block.",
         "module": "app/dtl/cost_governor.py"},
        {"layer": "DEFENSE", "component": "ML Detector",
         "role": "Catches behavioural/semantic risk the invariants do not encode. Advisory to the DTL, not a replacement.",
         "module": "app/detector/"},
        {"layer": "DEFENSE", "component": "Explainability (SHAP)",
         "role": "Attributes every model score to specific features.",
         "module": "app/detector/explainability.py"},
        {"layer": "DEFENSE", "component": "PQC Audit",
         "role": "ML-DSA-44 signatures over the hash-chained event log, so the record of what was contained is unforgeable.",
         "module": "app/crypto/"},
    ],
    "intelligence_layer": {
        "name": "AI agents",
        "count": len(AGENT_CATALOG),
        "rule": "Advisory only. The LLM explains, translates and proposes; every proposal is "
                "schema-validated and re-checked by the deterministic engine before it can "
                "affect anything.",
        "lifecycle": [
            "Intent Compiler turns human instruction into a machine-checkable authority vector",
            "-> DTL enforces that vector deterministically",
            "-> Event Explainer / Log Copilot make the enforcement legible",
            "-> Policy Advisor / Counterfactual Analyst propose changes, simulator judges them",
            "-> Incident Report / Customer Notice handle the aftermath",
        ],
    },
    "headline": "Delegated authority -> multidimensional attack -> deterministic invariant -> "
                "ML comparison -> explainable containment.",
}
