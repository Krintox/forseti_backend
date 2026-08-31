# LEARN_18 — The Agentic Payment Kill Chain

> **Prerequisites:** [LEARN_05](LEARN_05_ATTACKS_AND_SIMULATOR.md), [LEARN_16](LEARN_16_INTENT_FIREWALL.md), [LEARN_17](LEARN_17_DECEPTION_LAB.md)  
> **You will be able to:**
> - Recite the 11-stage agentic payment lifecycle and explain why it's a MAPPING layer, not a new attack surface.
> - Explain why a single arena round only ever exercises one stage, and why coverage is a session-level, not a round-level, concept.
> - Trace exactly how `time_to_detection_ms` and `economic_exposure_prevented_inr` are computed, and why nothing here is estimated.
> - Explain why stages 10-11 needed a THIRD parallel mechanism (Settlement Reconciliation) rather than another DTL invariant or Deception Lab detector.  
> **Files this chapter is about:** `backend/app/kill_chain/stages.py`, `backend/app/kill_chain/scoring.py`, `backend/app/arena/orchestrator.py`

---

## 1. Eleven Stages, One Taxonomy

🧒 **Like you're five**  
Think of a robber's plan as a comic strip with eleven panels: first they trick you, then they sneak past the guard, then they grab the wrong thing, and so on. FORSETI's Kill Chain is that comic strip drawn out in full, in advance, so that whenever an attack happens, you can point at exactly which panel it belongs to — instead of just saying "something bad happened."

🏪 **In real life**  
Security teams reason about attacks in terms of MITRE ATT&CK-style kill chains: which stage of the attacker's lifecycle did this incident represent? `backend/app/kill_chain/stages.py:25` defines an 11-stage lifecycle for agentic payments, from intent manipulation through settlement conflict.

🎓 **Properly**  
This module is explicitly a **mapping and scoring layer**, not a new attack surface: it answers "where in the agent's lifecycle did this ALREADY-EXISTING attack land," for the 16 vectors covered in LEARN_04, LEARN_05, LEARN_16, and LEARN_17, plus the 2 Settlement Reconciliation vectors covered in LEARN_21 — not another kind of thing to detect.

```
┌────────────────────────────────────────────────────────────────────────┐
│                    THE 11-STAGE AGENTIC PAYMENT LIFECYCLE              │
├────┬───────────────────────────┬───────────────────────────────────────┤
│ #  │ Stage                     │ Vector(s) mapped here                │
├────┼───────────────────────────┼───────────────────────────────────────┤
│ 1  │ INTENT_MANIPULATION       │ PROMPT_INJECTION                     │
│ 2  │ DELEGATION_ABUSE          │ REVOCATION_FLOOD, SCOPE_CREEP,       │
│    │                           │ LAPSED_MANDATE                       │
│ 3  │ GOAL_HIJACKING            │ CONSTRAINT_EROSION                   │
│ 4  │ MEMORY_POISONING          │ CONTEXT_MEMORY_POISONING             │
│ 5  │ TOOL_POISONING            │ TOOL_OUTPUT_POISONING,               │
│    │                           │ BASELINE_POISONING                   │
│ 6  │ MERCHANT_IMPERSONATION    │ BENEFICIARY_DRIFT                    │
│ 7  │ CART_SUBSTITUTION         │ INTENT_LAUNDERING                    │
│ 8  │ AUTHORITY_BYPASS          │ RAIL_SCOPE_VIOLATION, PER_TX_BREACH, │
│    │                           │ AUTHORITY_IMPERSONATION,             │
│    │                           │ VELOCITY_BURST                       │
│ 9  │ CROSS_RAIL_SPLIT          │ CROSS_RAIL_SPLIT (flagship)          │
│ 10 │ SETTLEMENT_CONFLICT       │ SETTLEMENT_CONFLICT (RECON_01)        │
│ 11 │ RECONCILIATION_DRIFT      │ RECONCILIATION_DRIFT (RECON_02)       │
└────┴───────────────────────────┴───────────────────────────────────────┘
```

Every vector maps to exactly **one** stage — its own primary mechanism — the same one-mapping-per-vector discipline `STRATEGY_DIMENSION` already applies in the orchestrator (LEARN_05). "This vector touches four stages a little" is not a checkable claim; one honest primary stage is.

### Why stages 10-11 needed a third mechanism, not another invariant

All 11 stages now have an implemented vector behind them (`STRATEGY_TO_STAGE` in `kill_chain/stages.py` has no unmapped entries left). `SETTLEMENT_CONFLICT` and `RECONCILIATION_DRIFT` were the last two, and closing them could **not** reuse the DTL invariant engine or Deception Lab: both vectors are designed to satisfy every one of the seven authority dimensions cleanly at authorization time, and neither involves the agent's own reasoning being deceived. The failure is entirely post-authorization — two settlement legs disagreeing with each other after both were individually, correctly authorised. `app/settlement/reconciliation.py` is the resulting THIRD parallel mechanism; see LEARN_21 for the full detail, including why it deliberately does not route through `cost_governor.py`'s invariant-code dispatch.

---

## 2. Why Coverage Is a Session Concept, Not a Round Concept

A single arena round runs **one** strategy, so it exercises exactly one stage. "Kill-chain coverage" only becomes meaningful across a **session** of multiple rounds — which is exactly how `ArenaBattleOrchestrator.round_history` (`backend/app/arena/orchestrator.py`) works: every completed round appends its `kill_chain` score to a list that survives until the operator calls `reset()`.

```python
# backend/app/kill_chain/scoring.py:110
def coverage(round_scores: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Rolls per-round scores up into session-level stage coverage."""
    ...
    return {
        "stages_reached": len(reached),          # distinct stages this session touched
        "stages_contained": stages_contained,     # of those, how many were contained
        "coverage_pct": ...,                      # stages_reached / 11
        "unmapped_rounds": unmapped_rounds,        # rounds with no stage mapping
    }
```

---

## 3. Per-Round Scoring: Nothing Is Estimated

`score_round()` (`backend/app/kill_chain/scoring.py:33`) computes five numbers, every one of them read from data the round already produced — nothing re-simulated:

```
┌────────────────────────────────────────────────────────────────────────┐
│              SCORE_ROUND() — WHERE EVERY NUMBER COMES FROM             │
├──────────────────────────────┬───────────────────────────────────────────┤
│ time_to_detection_ms          │ offset_ms of the first INVARIANT_        │
│                               │ VIOLATION (or DECEPTION_DETECTED) event  │
│                               │ minus offset_ms of ATTACK_STARTED.       │
├──────────────────────────────┼───────────────────────────────────────────┤
│ economic_exposure_prevented_  │ Sum of `overshoot` from every            │
│ inr                           │ INVARIANT_VIOLATION event's own payload  │
│                               │ — the proof's own arithmetic, re-read.   │
├──────────────────────────────┼───────────────────────────────────────────┤
│ blast_radius_score            │ HEURISTIC: distinct rails touched / 3.   │
│                               │ Labelled a heuristic, not measured.      │
├──────────────────────────────┼───────────────────────────────────────────┤
│ attack_chain_score            │ HEURISTIC composite: 0.5×contained +     │
│                               │ 0.3×speed + 0.2×(exposure prevented>0)   │
│                               │ — a simple, deterministic, DOCUMENTED    │
│                               │ formula, not a tuned model.              │
└──────────────────────────────┴───────────────────────────────────────────┘
```

### A real bug caught by the numbers, not by reading the diff

The very first version of `score_round()` read `round_summary["events"]` directly, not realising that field is `EventRecorder.timeline()` — the recorder's **cumulative** log across every round since the last explicit `reset()`, not just the round that just ran. Running two rounds back to back would have made every round after the first compute its `time_to_detection_ms` against the FIRST round's `ATTACK_STARTED` timestamp — silently wrong, and it would have looked plausible (a real number, just measuring the wrong window). The fix filters events to `event["round_id"] == round_number` before any offset lookup, and is pinned by a dedicated regression test (`test_second_round_is_not_confused_by_the_first_rounds_events`) plus a live multi-round smoke test through the real API with no reset in between.

---

## 4. Reading the Cards Live

The Kill Chain card on the Live Arena page (`frontend/app/arena/page.tsx`) shows, per round: the stage name, detection latency, chain score, exposure prevented, and blast radius — sourced directly from `lastRound.kill_chain`, which the backend already attaches to every round result. No extra frontend fetch is needed for the per-round view; `GET /api/arena/kill-chain` exists specifically for the session-level coverage rollup and the static stage taxonomy.

```
┌────────────────────────────────────────────────────────────────────────┐
│                  API SURFACE FOR THIS CHAPTER                          │
├────────────────────────────────────────────────────────────────────────┤
│ GET /api/arena/kill-chain  → 11-stage taxonomy + last round's score +  │
│                               session coverage across round_history    │
└────────────────────────────────────────────────────────────────────────┘
```

---

## Check yourself

1. **Why can a single arena round never exercise more than one kill-chain stage?**
2. **Why couldn't stages 10-11 reuse the DTL invariant engine, even though both new vectors clear every one of the seven authority dimensions?**
3. **What is `economic_exposure_prevented_inr` actually computed from?**
4. **Are `blast_radius_score` and `attack_chain_score` measured or heuristic, and how do you know?**
5. **What bug did the accumulated-event-log regression test catch, and why would it have gone unnoticed just from reading the code?**

<details>
<summary>Answers</summary>

1. Because each round runs exactly one strategy, and each strategy maps to exactly one primary stage in `STRATEGY_TO_STAGE`.
2. Because the DTL invariant engine only evaluates authority dimensions AT authorization time, and both vectors are designed to pass every one of those checks cleanly on both legs. The failure only exists in the disagreement between two settlement legs AFTER both were individually authorised — a post-authorization lifecycle question the seven invariants are not positioned to answer, which is why `app/settlement/reconciliation.py` exists as a third parallel mechanism (LEARN_21).
3. The `overshoot` field the invariant engine's own `INVARIANT_VIOLATION` proof already computed — summed across every violation event in the round, not re-derived.
4. Heuristic — both are explicitly documented in `scoring.py`'s docstring as composites with no external ground truth, unlike, say, `economic_exposure_prevented_inr`, which is a direct read of a value already computed elsewhere.
5. Running two rounds back to back without a reset would have made the second round's latency measured against the FIRST round's `ATTACK_STARTED` timestamp, since `EventRecorder.timeline()` accumulates across rounds — invisible from reading `score_round()` alone because the bug only manifests across multiple calls, not within one.
</details>

---

## Where to go next
→ [LEARN_19 — Graph Sentinel & the ML Feature Expansion](LEARN_19_GRAPH_SENTINEL.md)
