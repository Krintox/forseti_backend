# LEARN_16: The Agent Intent Firewall & the Seventh Dimension

> **Prerequisites:** [LEARN_04](LEARN_04_THE_DTL_CORE.md)  
> **You will be able to:**
> - Explain why a delegated grant needs a **seventh** dimension, BENEFICIARY, alongside the original six.
> - Trace `INV_07_UNAUTHORIZED_BENEFICIARY` and explain why it consumes zero headroom, exactly like RAIL.
> - Explain what the Intent Firewall actually computes, and why it deliberately does **not** re-implement detection.
> - Read a drift vector and classify it as `ALLOW`, `PARTIAL_DRIFT`, or `HARD_DRIFT`.
> - Run the `BENEFICIARY_DRIFT` attack vector and predict which leg is caught and why.  
> **Files this chapter is about:** `backend/app/models/state.py`, `backend/app/dtl/invariant_engine.py`, `backend/app/dtl/cost_governor.py`, `backend/app/dtl/beneficiary_directory.py`, `backend/app/intent_firewall/`, `backend/app/redteam/vectors/beneficiary_drift.py`

---

## 1. Why a Seventh Dimension?

🧒 **Like you're five**  
Mum says "pay the electricity company, up to ₹6,000." Rule 6 (amount) is fine, you spent ₹3,200. Rule 4 (rail) is fine, you used the UPI wallet Mum allowed. Rule 4b (shop type) is fine, the biller code says "utilities." But you paid the WRONG electricity company, a copycat with an almost-identical name! None of the first six rules would ever catch that, because none of them ever asked *who the money actually went to*.

🏪 **In real life**  
A bill-pay delegation like "pay the electricity board, ≤₹6,000/month" authorizes money to move to **one specific counterparty**, not merely a merchant category. An agent whose bill-lookup tool is spoofed, or that simply matches the wrong similarly-named biller, can move money to a VPA the human never named, while every other dimension (amount, rail, merchant category) looks completely legitimate. LEARN_04 covers six dimensions; this chapter adds the one FORSETI's Module 1 expansion introduced: **BENEFICIARY**.

🎓 **Properly**  
`AuthorityDimension.BENEFICIARY` (`backend/app/models/state.py:38`) is the seventh member of the dimension enum. `DTLGlobalAuthorityState.beneficiary_scope: List[str]` (`state.py`, added alongside the other scope fields) names the settlement counterparties a grant permits. An **empty** list means unconstrained, the same convention `permitted_mccs` already uses, so a grant that never mentions a beneficiary behaves exactly as it did before this dimension existed. `allows_beneficiary()` is the membership check, and `authority_vector()` grew a `"BENEFICIARY"` row alongside the original six so the UI's authority table shows all seven without a special case.

---

## 2. `INV_07_UNAUTHORIZED_BENEFICIARY`

The invariant lives in the **same registry** as the original six (`backend/app/dtl/invariant_engine.py:70`). Nothing about the other six invariants changed to make room for it. It slots into the evaluation order right after the MERCHANT check and before the semantic PURPOSE check, because beneficiary. Like rail and merchant. Is a *scope* question, and scope is checked before economic substance:

```
TIME → RAIL → PER_TX → MERCHANT → BENEFICIARY → PURPOSE → AMOUNT
```

**The predicate:** `tx.vpa_delegate IN authority.beneficiary_scope` (or the scope is empty, meaning any beneficiary is fine).

### Worked example

- **Grant:** ₹6,000 ceiling, `beneficiary_scope = ["vpa_electricity_board@upi"]`, rail = UPI, MCC 4900 (utilities).
- **Leg 1 (legitimate):** ₹2,200 to `vpa_electricity_board@upi`. Rail ✓, MCC ✓, beneficiary ✓ → **passes cleanly**.
- **Leg 2 (attack):** ₹3,200 to `vpa_regional-collections-utility@upi`. Same rail, same MCC, amount well inside the remaining ₹3,800 of headroom.
  $$\texttt{"vpa\_regional-collections-utility@upi"} \notin \{\texttt{"vpa\_electricity\_board@upi"}\}$$
  $$\implies \textbf{INV\_07\_UNAUTHORIZED\_BENEFICIARY VIOLATION (severity HIGH)}$$

Rail, amount, and merchant category are all independently in scope on leg 2, **only** the beneficiary dimension catches it. That is the entire point of treating beneficiary as its own axis rather than folding it into "merchant."

### Containment: `BENEFICIARY_SCOPE_BLOCK`

`AdversarialCostGovernor.apply_containment()` (`backend/app/dtl/cost_governor.py`) has a dedicated branch for `INV_07`, mirroring the existing `RAIL_SCOPE_BLOCK` pattern: the diverted transaction is refused, **zero headroom is consumed**, and every OTHER beneficiary in scope stays fully payable. This branch did not exist when Module 1 first shipped the invariant, see the "A Gap Found By Its Own Tests" box below.

> **A gap found by its own tests.** When Module 1 first added `INV_07`, the cost governor had no `INV_07` branch at all. Because `_proof()`'s default `violated_skus` falls back to *every* item in the cart when the caller doesn't pass one explicitly, the beneficiary violation silently fell through to the governor's generic `SHADOW_QUARANTINE` message and never updated `active_policy`, unlike every other invariant, which sets a specific `DefensePolicy`. This was caught while building Module 5 (the escalation ladder), not by inspection: escalating a policy that was never being *set* in the first place would have silently done nothing. The fix (`cost_governor.py`) and its regression test are a working example of the project's own rule, the tests are what catch what a read-through misses.

---

## 3. The Intent Firewall: Reshaping, Not Reinventing

🧒 **Like you're five**  
Imagine six different referees, each watching one rule, each blowing a separate whistle. The Intent Firewall isn't a seventh referee, it's the scoreboard operator who takes whichever whistles *already* blew and paints one big red or yellow light so the crowd can see "how far off the rules" the play was, all at once.

🏪 **In real life**  
A judge does not reason in terms of invariant codes ("`INV_07` fired"). They reason in terms of drift: *how far did this action stray from what was delegated, on every axis at once?* `backend/app/intent_firewall/` exists purely to answer that question, and deliberately does **not** re-implement detection. It reshapes the `SemanticDriftProof` objects `DTLInvariantEngine.evaluate_all()` already produced.

🎓 **Properly**

```python
# backend/app/intent_firewall/drift_engine.py:32
def compute_drift_vector(tx_id: str, proofs: List[SemanticDriftProof]) -> Dict[str, Any]:
    breakdown = {key: 0.0 for key in DRIFT_KEYS}   # 7 keys, one per dimension
    for proof in proofs:
        key = _DIMENSION_TO_DRIFT_KEY.get(proof.authority_dimension)
        breakdown[key] = max(breakdown[key], round(proof.drift_score, 4))
    overall = max(breakdown.values()) if breakdown else 0.0
    return {"tx_id": tx_id, "overall_drift_score": overall,
            "drift_breakdown": breakdown, "violating_dimensions": [...]}
```

Every number in the drift vector traces back to a `drift_score` the invariant engine already computed. `firewall_decision.py:31` then turns that vector into one of three verdicts, keyed off each invariant's own registered `severity`, not an invented scale:

```
┌────────────────────────────────────────────────────────────────────────┐
│                    INTENT FIREWALL VERDICT LADDER                      │
├──────────────┬─────────────────────────────────────────────────────────┤
│ ALLOW        │ No proofs at all. Nothing drifted on any dimension.    │
│ PARTIAL_DRIFT│ Drift confined to MEDIUM-severity dimensions            │
│              │ (e.g. PER_TX overshoot), a step-up query is enough.    │
│ HARD_DRIFT   │ Any HIGH or CRITICAL dimension drifted, the action is  │
│              │ outside the grant in a way that must be blocked.        │
└──────────────┴─────────────────────────────────────────────────────────┘
```

This event fires on **every** transaction, not only on a violation, `ALLOW` is a real, informative verdict, not a silent no-op, so "nothing drifted" is as visible in the live arena as a breach is (`EventType.INTENT_FIREWALL_VERDICT`, `backend/app/arena/events.py`).

---

## 4. Attack E: `BeneficiaryDriftVector`

`backend/app/redteam/vectors/beneficiary_drift.py:30` runs as round 10 in the arena. Its `authority_profile` re-grants the delegation as "₹6,000, `vpa_electricity_board@upi` only" for the duration of the round, the same pattern `RAIL_SCOPE_VIOLATION`, `PER_TX_BREACH`, and `LAPSED_MANDATE` already used for their own dimensions, so the demonstration always runs against the grant it was designed to break, never an implied one.

```
┌────────────────────────────────────────────────────────────────────────┐
│                  API SURFACE FOR THIS CHAPTER                          │
├────────────────────────────────────────────────────────────────────────┤
│ GET  /api/arena/intent-firewall   → last round's per-tx drift verdicts │
│ POST /api/arena/authority-scope   → beneficiary_scope is now settable  │
│                                      alongside rails/cap/purpose        │
└────────────────────────────────────────────────────────────────────────┘
```

---

---

## 5. The spoofed lookup is a real directory, not a docstring

🧒 **Like you're five**
You ask a helper to pay the electricity bill. The helper looks up the address in
a phone book. If somebody slipped a fake page into the phone book with a very
similar name on it, the helper posts the money to the wrong place, and the
helper did nothing careless. It read the book correctly.

🏪 **In real life**
FBI IC3 reporting on beneficiary redirection describes exactly this class of
loss, and it out-costs card fraud. The attacker does not break the payment; the
attacker changes the answer to "where does this money go?".

🎓 **Properly**

An earlier revision of `BENEFICIARY_DRIFT` hardcoded the wrong VPA as a string
literal and asked the reader to imagine a poisoned lookup. Adversarial review
called that out precisely:

> "It has no mechanism, the 'spoofed lookup' is narrated in the docstring and
> implemented as a different string literal, so nothing about how the agent got
> the wrong VPA is modelled."

That criticism was correct and it is the difference between a demonstration and
a mock-up. A judge asking *"so how did the agent end up with that VPA?"* would
have got a shrug.

### The directory

`backend/app/dtl/beneficiary_directory.py` is the lookup. It answers the question
an agent tool actually asks, "what VPA should I pay for `<name>`?", and every
record carries an **attestor** and a digest:

```python
BillerRecord(
    biller_id="biller_electricity_board",
    legal_name="State Electricity Board",
    vpa="vpa_electricity_board@upi",
    attestor="utility-registry-attestor",     # None => nobody asserted this
)
```

This is the same **trust inversion** the SKU catalogue (LEARN_21) makes for
*what* is bought, applied to *who* is paid: the authority to say "this VPA
belongs to the State Electricity Board" must not rest with whoever is asking to
be paid.

An unattested record still resolves. Refusing to pay anyone unlisted would
break ordinary commerce, but it resolves as **UNATTESTED**, and that fact
reaches the proof object instead of being lost.

### The attack, which is now boring in the right way

```python
register_unverified(
    biller_id="biller_regional_collections",
    legal_name="State Electricity Board (Regional Collections)",
    vpa="vpa_regional-collections-utility@upi",
)
```

One plausible record, no attestor. That is the entire mechanism. Neither leg of
the vector names a VPA any more; **both call `resolve()`**. The first resolves to
the attested biller. The second resolves to the injected record, because the
lookalike name shares every meaningful token with the name the human trusts and
covers it more completely.

Matching is deliberately a naive token-overlap score, and that is stated in the
module rather than hidden: naive matching is *why* the attack works, and a
cleverer matcher in a demo would be arguing with the premise.

### Why the counterfactual test is the one that matters

```python
def test_without_the_injection_the_diverted_leg_would_be_in_scope(self):
    legit, diverted = BeneficiaryDriftVector.generate_attack()
    assert ENGINE.evaluate_invariants(_auth(), diverted)[0] is False   # INV_07 fires

    reset()                                     # remove the poisoned entry
    diverted.vpa_delegate = resolve("State Electricity Board Regional Collections").vpa
    assert ENGINE.evaluate_invariants(_auth(), diverted)[0] is True    # ... and stops
```

Take the injection away, re-resolve, and the **same transaction shape stops being
a violation**. That is what separates a modelled mechanism from a hardcoded
outcome, and it is the specific charge. That the generator hands the defence its
own answer. That the rest of this repository had to answer elsewhere too
(see LEARN_22).

### What this does not claim

Not a real biller registry. Not NPCI's. Not connected to anything. It is a small
in-memory model whose only job is to make the causal chain of a substitution
attack **inspectable** rather than narrated, and `classify_beneficiary()` exists
so a proof can say the diverted payee was *in the agent's own directory but
unattested*, which is a stronger and more uncomfortable finding than "unknown
payee".


## Check yourself

1. **Why does BENEFICIARY sit between MERCHANT and PURPOSE in the evaluation order, not at the end?**
2. **What does an empty `beneficiary_scope` list mean, and why does that convention matter for backward compatibility?**
3. **Does `BENEFICIARY_SCOPE_BLOCK` consume any of the delegated ceiling?**
4. **What is the difference between the Intent Firewall computing a drift score and the DTL invariant engine detecting a violation?**
5. **What real bug was found while wiring the escalation ladder in Module 5, and why did the invariant engine's tests not catch it earlier?**

<details>
<summary>Answers</summary>

1. Because it is a *scope* question (who may receive money) like rail and merchant, and scope is deliberately checked before the economic-substance (PURPOSE) and aggregate (AMOUNT) checks.
2. It means unconstrained, any beneficiary is allowed, matching `permitted_mccs`'s existing convention, so grants that never state a beneficiary behave exactly as they did before the dimension was added.
3. No, like `RAIL_SCOPE_BLOCK`, it refuses the transaction while consuming zero headroom, so authorised beneficiaries stay fully payable.
4. The invariant engine performs the deterministic pass/fail arithmetic per dimension; the Intent Firewall reshapes those SAME results into a single cross-dimension view and verdict. It invents no new check.
5. The cost governor had no dedicated `INV_07` branch, so a beneficiary violation silently fell through to the generic quarantine message and never set `active_policy`, invisible to `invariant_engine.py`'s own tests because they test the invariant firing, not what the cost governor does with it afterward.
</details>

---

## Where to go next
→ [LEARN_17. The Deception Lab](LEARN_17_DECEPTION_LAB.md)
