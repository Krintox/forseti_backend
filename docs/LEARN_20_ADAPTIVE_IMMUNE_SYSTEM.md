# LEARN_20: The Adaptive Immune System & the Unified Risk Engine

> **Prerequisites:** [LEARN_04](LEARN_04_THE_DTL_CORE.md), [LEARN_18](LEARN_18_KILL_CHAIN.md)  
> **You will be able to:**
> - Explain what "adaptive" meant BEFORE this module (Red only) and what it means AFTER (Red and Blue both).
> - Trace the exact escalation ladder. Which policy fires on occurrence 1, 2, and 3+ of the same invariant.
> - Explain why the campaign runner deliberately does NOT recreate the "blocked by Graph Sentinel" narrative some design documents describe, and what it does instead.
> - Read a Unified Risk Engine response and explain why `deterministic_override` matters more than the headline `overall_risk_score`.  
> **Files this chapter is about:** `backend/app/feedback/policy_adapter.py`, `backend/app/feedback/feedback_engine.py`, `backend/app/models/state.py`, `backend/app/arena/orchestrator.py`, `backend/app/risk_engine/risk.py`

---

## 1. A Defender That Reacts Identically Isn't Really Defending

🧒 **Like you're five**  
If a kid keeps sneaking cookies from the same jar, and every single time Mum just says "no cookies" in exactly the same calm voice, the kid learns nothing changes, so they keep trying. A REAL grown-up gets sterner each time: first a warning, then cookies get moved to a high shelf, then the whole kitchen gets a lock. FORSETI's Blue side used to be the calm-voice parent. This module makes it the real one.

🏪 **In real life**  
Before this module, `BluePolicyAdapter.adapt_policy()` (`backend/app/feedback/policy_adapter.py`) already existed and was already wired live into the orchestrator, it is **not** new. It mapped each invariant code to a fixed containment policy: `INV_01` → tighten headroom, `INV_02` → strict catalog attestation, anything else → step-up verification. What was missing, and what this module actually adds. Is **escalation**: the exact same response fired every single time, no matter how many times Red had already been caught doing the identical thing.

🎓 **Properly**  
This is worth stating precisely, because getting it right matters for how you talk about this module: the Red-side adaptive planner (`feedback/adaptive_planner.py`) already picked its next strategy from observed outcomes before this session began. What this module adds is the **symmetric Blue-side half**, the response strength itself now escalates with repetition, closing the loop the module's name promises.

---

## 2. The Two-Rung Escalation Ladder

```python
# backend/app/feedback/policy_adapter.py:31
_ESCALATION: Dict[int, DefensePolicy] = {
    2: DefensePolicy.CAPABILITY_QUARANTINED,
    3: DefensePolicy.AGENT_SUSPENDED,
}
```

```
┌────────────────────────────────────────────────────────────────────────┐
│                    THE BLUE ESCALATION LADDER                          │
├──────────────────┬───────────────────────────────────────────────────────┤
│ Occurrence 1      │ The ORIGINAL per-invariant soft response, unchanged: │
│ of this invariant │  INV_01 → TIGHTENED_HEADROOM_V2                     │
│ this session       │  INV_02 → STRICT_CATALOG_ATTESTATION                │
│                   │  anything else → STEP_UP_VERIFICATION               │
├──────────────────┼───────────────────────────────────────────────────────┤
│ Occurrence 2      │ CAPABILITY_QUARANTINED. Regardless of WHICH        │
│                   │ invariant it is. Agent capability downgraded;       │
│                   │ future transactions need operator confirmation.     │
├──────────────────┼───────────────────────────────────────────────────────┤
│ Occurrence 3+     │ AGENT_SUSPENDED (capped, a 9th occurrence still    │
│                   │ reports AGENT_SUSPENDED, not something further).    │
│                   │ Mandate paused pending fresh re-consent.             │
└──────────────────┴───────────────────────────────────────────────────────┘
```

`DefensePolicy.AGENT_SUSPENDED` (`backend/app/models/state.py`) is a new, additive enum value, the ceiling of the ladder.

### Where the count actually comes from

`ClosedLoopFeedbackEngine.record_round_outcome()` (`backend/app/feedback/feedback_engine.py`) counts **prior** occurrences of the SAME `violating_invariant` in its own memory **before** appending the new record, so a first-time violation still correctly gets `violation_count=1` (the soft response), never the escalated one:

```python
prior_hits = sum(1 for r in self.memory.history if r.violating_invariant == violating_invariant)
blue_desc, changes = self.blue_adapter.adapt_policy(
    auth_state, violating_invariant, violation_count=prior_hits + 1
)
```

A **different** invariant starts its own count from zero. Escalation is per-invariant, not global, so pressing `RAIL_SCOPE_VIOLATION` twice does not pre-escalate a first attempt at `LAPSED_MANDATE`.

---

## 3. The Campaign Runner: Honesty Over a Cleaner Story

`ArenaBattleOrchestrator.run_campaign()` (`backend/app/arena/orchestrator.py:943`) runs a server-orchestrated sequence of rounds in one call, so escalation state genuinely accumulates across them:

```python
DEFAULT_CAMPAIGN: List[int] = [7, 7, 7]   # RAIL_SCOPE_VIOLATION, three times
```

**Why not the "5-round scripted campaign" some design documents describe**, Round 1 blocked by DTL, Round 2 by the Intent Firewall, Round 3 "blocked by Graph Sentinel," Round 4 a composite attack? Because Round 3's claim isn't true of THIS live system: Graph Sentinel (LEARN_19) is a training-time feature pipeline, never evaluated per-transaction in the live single-authority arena. Scripting a moment where the live UI claims Graph Sentinel caught something it structurally cannot see would misrepresent what the system actually does. The default campaign instead runs the SAME strategy three times, a smaller, less cinematic claim, but one the live arena can prove end to end: the full escalation ladder, watched in real time.

A campaign accepts **any** custom round sequence (`round_numbers`), not just the default, the multi-vector campaign already in `ArenaControls.tsx` (client-side, loops `runRound()` over DIFFERENT strategies) is the complementary case: it can show Red adapting strategy, but because it never repeats one strategy, it structurally cannot exercise the escalation ladder. The two features are not redundant.

```
┌────────────────────────────────────────────────────────────────────────┐
│                  API SURFACE FOR THIS CHAPTER                          │
├────────────────────────────────────────────────────────────────────────┤
│ POST /api/arena/campaign  → { round_numbers?, dtl_enabled, speed }     │
│                              runs the sequence, returns every round's   │
│                              result + session kill-chain coverage +     │
│                              final_active_policy                       │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 4. The Unified Risk Engine: A Synthesis, Not a Detector

🧒 **Like you're five**  
Five different teachers each grade one part of your report card, math, reading, gym, art, music. The Unified Risk Engine is the office assistant who averages all five grades onto one summary line for the principal to glance at. That assistant doesn't re-grade your work, and if math failed you outright, the principal already knows that from the math teacher, not from the average.

🏪 **In real life**  
`backend/app/risk_engine/risk.py:17`'s `compute_unified_risk()` combines five signals **every other module in this course already computed** for a round. It invents no signal of its own:

```
┌────────────────────────────────────────────────────────────────────────┐
│                  THE FIVE RISK COMPONENTS                              │
├───────────────────────┬─────────────────────────────────────────────────┤
│ dtl_invariant_risk     │ 1.0 if the round was detected, else 0.0        │
│ intent_firewall_risk   │ max overall_drift_score across firewall_       │
│                        │ verdicts (LEARN_16)                             │
│ deception_lab_risk     │ 1.0 if any deception was DETECTED (LEARN_17)   │
│ ml_anomaly_risk        │ max ML probability across step_results         │
│ kill_chain_risk        │ attack_chain_score (LEARN_18)                   │
└───────────────────────┴─────────────────────────────────────────────────┘

overall_risk_score = mean(the five components above)   ← EQUAL weighted
```

🎓 **Properly**, Why equal-weighted, and why say so out loud? Because there is no labelled dataset of "true" incident severity to fit weights against. Presenting a weighted formula with numbers like `0.4, 0.3, 0.2, 0.05, 0.05` would look more sophisticated and be **less honest**, it would imply a rigor that was never actually fitted. An equal-weighted mean, stated as exactly that, is the more defensible claim.

### `deterministic_override` is the field that actually matters

```json
{
  "overall_risk_score": 0.72,
  "risk_components": { "...": "..." },
  "deterministic_override": true,
  "note": "A synthesis of signals this round already produced, not a new detector. The DTL invariant decided the outcome before this score was computed."
}
```

`deterministic_override` is always `true` exactly when the DTL actually contained the round. Making explicit that **this score never decided anything**. The invariant decided the outcome; the risk score was computed afterward, purely as a summary. This is the same architectural boundary LEARN_09 draws for the 12 AI agents ("the LLM never enforces"), applied to a numeric synthesis instead of a language model.

---

## Check yourself

1. **What did `BluePolicyAdapter` already do BEFORE this module, and what specifically did this module add?**
2. **On the 2nd occurrence of the SAME invariant this session, what policy fires, regardless of which invariant it is?**
3. **Why does a different invariant start its own escalation count from zero?**
4. **Why doesn't the default campaign recreate the "blocked by Graph Sentinel" round from the original design narrative?**
5. **What does `deterministic_override: true` actually tell you about the Unified Risk Engine's role in the outcome?**

<details>
<summary>Answers</summary>

1. It already mapped each invariant to a fixed containment policy and was already wired live. This module added ESCALATION, the response strength growing with repeated occurrences of the same invariant, which previously always produced the identical response.
2. `CAPABILITY_QUARANTINED`, the ladder's 2nd rung fires the same way no matter which invariant triggered it.
3. Because escalation is tracked per-invariant in `record_round_outcome()`'s prior-occurrence count, not globally. Repeating one attack does not pre-escalate a different one.
4. Because Graph Sentinel's entity graph is a training-time construct, never evaluated per-transaction in the live single-authority arena, claiming it "caught" something live would misrepresent what the system actually does.
5. It tells you the risk score never decided the round's outcome, the DTL invariant already had, before the composite score was even computed. The score is a summary, not a gate.
</details>

---

## The two ways a ladder stops being real

An escalation ladder is a claim about consequences. It is worth exactly as much
as the enforcement behind it, and this one failed that test twice, in two
different places, for two different reasons. Both failures are instructive
because neither showed up as a bug: the code ran, the tests were green, and the
screen looked right.

### Failure 1: the top rung enforced nothing

Adversarial review asked: *"show me the line where `AGENT_SUSPENDED` causes a
transaction to be rejected."*

There was no such line. `adapt_policy()` set `auth.active_policy` and adjusted a
few knobs, and **no authorization path ever read the result**. An agent whose
mandate was "suspended" transacted exactly as before. The ladder was a label
printed next to a payment that still went through.

The fix is `_check_agent_suspended` in `backend/app/dtl/invariant_engine.py`,
evaluated **first** in `evaluate_all()`. Before time, rail, cap, MCC,
beneficiary, purpose and budget. Because a suspended mandate authorises nothing
at any amount on any rail, the same reasoning that puts expiry near the front:

```python
def _check_agent_suspended(self, auth, tx, now):
    if not auth.policy_suspends_all_spend:
        return None
    return self._proof(..., code="INV_08_MANDATE_SUSPENDED",
                       expression="ASSERT authority.active_policy != AGENT_SUSPENDED")
```

`INV_08` is registered with `kind: "policy_state"`, **not** as an eighth
authority dimension. The seven dimensions describe what the principal granted;
this describes what the system has since withdrawn. Conflating them would have
inflated the dimension count, which is the kind of small dishonesty that costs
more than it buys.

The proof that it works is a **control**, not an assertion. In
`tests/test_suspension_is_enforced.py`, one entirely innocuous transaction, ₹100,
permitted rail, permitted MCC, in-scope basket, live mandate. Is run against
every rung of the ladder:

- under all seven other rungs it is **allowed** (containment without lockout: a
  narrowed authority is still an authority)
- under `AGENT_SUSPENDED` it is **rejected**, on every rail, at every amount
- when it *also* breaches the ceiling and the MCC scope, `INV_08` is still the
  **first** proof returned, so the UI explains the real cause

Without the control half, the test would pass just as happily against an engine
that rejected everything.

### Failure 2: the top rung was invisible

Fixing the engine was not enough, and this is the part worth remembering.

The Policy Center page kept **its own hand-written array** of the ladder,
maintained by hand alongside the `DefensePolicy` enum. The two drifted. The
frontend copy never listed `AGENT_SUSPENDED`, so when Blue escalated all the
way to the top rung, the page matched nothing and highlighted nothing. The most
severe state in the system rendered as *no active policy at all*.

Nothing caught this. It is invisible to type checking (the array was
well-typed), invisible to the backend suite (the backend was correct), and
invisible to a component test (the component rendered its props faithfully). It
took driving the real UI against the real backend, all the way through a
17-vector campaign, to see it.

The repair is structural rather than a corrected list. `POLICY_LADDER` is built
from the enum in `backend/app/models/state.py`, guarded at import time:

```python
assert {r["code"] for r in POLICY_LADDER} == {p.value for p in DefensePolicy}, (
    "POLICY_LADDER must cover every DefensePolicy member exactly once"
)
```

…served to the UI in the arena state payload alongside a live `policy_overlay`,
and rendered directly. The page now also shows what the active policy is
*withholding right now*. Ceiling withheld, effective per-transaction cap,
whether spend is suspended outright, so the rung is not just named but
quantified. If a future policy is ever added to the enum and not to the ladder,
the process refuses to start; if the active policy is somehow absent from the
published ladder, the page says so in red rather than silently highlighting
nothing.

`tests/test_policy_ladder.py` pins the rest: dense ordered rungs, every rung
carrying both a description and an enforced effect, `AGENT_SUSPENDED` at the
top, and, the one most likely to rot, that the **prose on each rung quotes the
number the engine actually enforces**, not a number someone typed once.

### The transferable lesson

Both failures share a shape: a value was *written* somewhere and *believed*
somewhere else, with nothing connecting the two. The first was policy state that
no check read. The second was a ladder the UI re-declared instead of receiving.

Neither is caught by more unit tests of either side. They are caught by removing
the second copy, an invariant check in the engine, a generated list from the
enum, and by testing the two halves *together*, live.


---

One more piece of the runtime has no chapter yet: this module's kill-chain scoring assumed two stages, SETTLEMENT_CONFLICT and RECONCILIATION_DRIFT, had no implemented vector. That gap is now closed.

→ Continue to [LEARN_21. Tokenization & Settlement Reconciliation](LEARN_21_TOKENIZATION.md)
