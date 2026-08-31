# FORSETI: Authority Model Upgrade & Architecture Review

<!--historical-record-->
> **HISTORICAL RECORD, the numbers below were true when this was written and several
> are now superseded.** This document is kept as a record of what was measured and
> decided at the time, so it is deliberately NOT updated when results change.
>
> For current figures see [`MEASURED_NUMBERS.md`](MEASURED_NUMBERS.md), which is
> generated from the artifacts that ship. Notably superseded here: the pre-leak-fix
> cross-rail recalls, PR-AUC, and every DTL/graph feature-lift value, see
> [`LEARN_22_THE_LEAK.md`](LEARN_22_THE_LEAK.md) for why they changed.


> **Document type:** Change record + design rationale
> **Date:** 2026-08-18
> **Scope:** Response to an external architecture assessment, the changes made as a result,
> the changes deliberately *not* made, and a full audit of every metric claimed in the docs.

---

## 0. TL;DR

Two things were requested and both are done:

1. **Clicking a step in the Live Attack Flow now explains that step.** Previously only the
   event log was clickable. Now every box on the flow diagram is a button that opens a
   panel explaining what that component is, what it can and cannot see, and its live state.
2. **The external assessment was evaluated, largely accepted, and implemented.** The core
   conceptual upgrade, *authority is multidimensional, not just a spend ceiling*, is now
   real code with six enforced invariants, three new attack vectors, and 15 new tests.

A third thing was done unprompted because the assessment demanded it: **every metric in the
documentation was audited against the artifacts.** Eleven figures were stale and wrong. They
are corrected in §6.

---

## 1. The assessment, evaluated honestly

The assessment made five substantive claims. Here is my verdict on each.

| # | Claim | Verdict | Action |
|---|---|---|---|
| 1 | Don't present 12 AI agents as 12 independent innovations; show the hierarchy | **Correct** | Implemented (§5) |
| 2 | Don't hide the "NOT working" section, 0% ML recall, 9% FPR make it credible | **Correct** | Kept, and made *more* prominent |
| 3 | Don't claim numbers unless reproducible from artifacts | **Correct, and we were violating it** | Full audit, 11 fixes (§6) |
| 4 | Authority is multidimensional; a rail-restricted grant breaks the flagship attack | **Correct and the most valuable point** | Core rework (§3) |
| 5 | Thesis should become "preserve delegated authority across every dimension" | **Correct** | Adopted (§2) |

### Where the assessment was right in a way that mattered

The sharpest observation was this: if a user says *"₹12,000, **only** through UPI"*, then
the flagship cross-rail split attack **does not work**, and, worse, the old FORSETI could
not even express that grant. `DTLGlobalAuthorityState` had a `global_budget_ceiling` and an
MCC list, and nothing else. There was no field for permitted rails, no per-transaction cap,
and no validity window.

That is a genuine architectural gap, not a presentation problem. It is now fixed.

### Where I partially disagree with the assessment

The assessment implies the old thesis ("payment systems don't know the total amount across
rails") was *too narrow to be interesting*. I'd put it differently, and this distinction
matters when presenting:

- The cross-rail aggregate blind spot is **real, specific, and measurable**. It is the one
  claim in this project with a hard number behind it (`dtl_invariant_only` catches 90.54% of
  a held-out attack family that every learned model scores at 0.0% recall).
- The multidimensional framing is **strictly better as a thesis**, but it is *broader*, and
  breadth without measurement is how projects start over-claiming.

So the framing is now multidimensional, but the **evidence** still rests on the dimension we
actually measured. The new dimensions are enforced deterministically and unit-tested; they
are **not** claimed to have measured detection rates, because they do not have any. §6.3
states this explicitly.

---

## 2. The thesis, restated

**Before:** "Payment rails cannot see each other's spend, so an agent can split a purchase
across them and exceed the user's budget."

**After:** "A delegated agent may act only within the authority a human granted, and that
authority is multidimensional. Amount is one dimension of six."

```
                        USER AUTHORITY
                              │
   ┌──────────┬───────────┬───┴───┬────────────┬──────────┐
   ▼          ▼           ▼       ▼            ▼          ▼
 AMOUNT    PER_TX       RAIL   MERCHANT     PURPOSE     TIME
 ₹12,000   ≤₹3,000    UPI only  MCC 5411   groceries   7 days
   │          │           │       │            │          │
 INV_01     INV_05     INV_04   INV_03      INV_02     INV_06
```

The key insight the assessment supplied: **an attacker does not need to exceed the money
limit to act outside the grant.** Spending ₹12,000 of a ₹12,000 budget on gift cards, on a
rail the user never authorised, or a day after the mandate lapsed, are all authority
violations at zero overspend.

---

## 3. What changed in the backend

### 3.1 The authority vector (`app/models/state.py`)

`DTLGlobalAuthorityState` gained the non-monetary dimensions of a grant:

```python
permitted_rails: List[PaymentRailType]   # "UPI only" is now expressible
per_transaction_cap: Optional[float]      # None = unconstrained
validity_window_hours: float = 168.0      # 7 days
economic_purpose: str
```

Plus helpers: `expires_at`, `is_expired(now)`, `allows_rail(rail)`, and
`authority_vector()`. Which returns one row per dimension and is what the UI renders.

**Back-compat is deliberate and tested:** the defaults are all-rails-permitted, no
per-transaction cap, 7-day window. The historical ₹10,000 demo behaves exactly as before
(`test_default_grant_is_unconstrained_on_the_new_dimensions`).

### 3.2 Six invariants (`app/dtl/invariant_engine.py`)

The engine was rewritten from two hardcoded checks into an ordered, registry-driven
evaluator.

| Invariant | Dimension | Predicate | Status before |
|---|---|---|---|
| `INV_06_AUTHORITY_EXPIRED` | TIME | `now <= created_at + window` | **new** |
| `INV_04_UNAUTHORIZED_RAIL` | RAIL | `tx.rail ∈ permitted_rails` | **new** |
| `INV_05_PER_TX_CAP_EXCEEDED` | PER_TX | `tx.amount <= per_transaction_cap` | **new** |
| `INV_03_UNAUTHORIZED_MCC` | MERCHANT | `tx.merchant_mcc ∈ permitted_mccs` | **documented but never enforced** |
| `INV_02_SEMANTIC_INTENT_DRIFT` | PURPOSE | `cart ∉ semantic_exclusions` | existed |
| `INV_01_GLOBAL_BUDGET_EXCEEDED` | AMOUNT | `Σ exposure + tx <= ceiling` | existed |

> **A real bug found and fixed:** `INV_03_UNAUTHORIZED_MCC` was named in the engine's own
> docstring and scored by the offline probe in `detector/baselines.py`, but **was never
> implemented in the runtime engine**. The `SCOPE_CREEP` vector (MCC 5311, outside the
> permitted grocery scope) was only ever caught when it happened to breach the budget. The
> measurement and the engine now agree.

Ordering is deliberate: expiry first (a lapsed grant authorises nothing), then scope, then
economic substance, then the aggregate ceiling.

`evaluate_all()` returns **every** violated dimension, so one transaction that breaks the
grant several ways reports all of them; `evaluate_invariants()` keeps the old
`(is_valid, first_proof)` signature so nothing downstream broke.

### 3.3 Containment is now proportionate to the dimension (`app/dtl/cost_governor.py`)

This is the part that keeps the "graceful containment" claim honest across the new
dimensions. The response depends on *which* dimension failed:

| Dimension | Response | Books money? |
|---|---|---|
| PURPOSE | `PARTIAL_AUTH`. Clear the genuine basket, quarantine stored value | yes, the legitimate part |
| AMOUNT | `HEADROOM_CAP`. Authorise exactly the remaining headroom | yes, up to headroom |
| PER_TX | `STEP_UP_REQUIRED`, user confirms; agent keeps transacting under the cap | **no** |
| RAIL | `RAIL_SCOPE_BLOCK`. Permitted rails stay fully usable | **no** |
| MERCHANT | `SCOPE_QUARANTINE`, shadow ledger; in-scope merchants unaffected | **no** |
| TIME | `RE_CONSENT_HOLD`. Held pending a fresh grant | **no** |

The four scope violations consume **zero** headroom. Refusing an out-of-scope action must
not spend the user's authority, verified by `test_rail_containment_consumes_no_headroom`.

### 3.4 Three new attack vectors (`app/redteam/vectors/authority_scope.py`)

Each carries the delegation profile it is designed to be run against, because *"UPI only"*
proves nothing against an all-rails grant. The arena re-grants the authority, **emits it as
an `AUTHORITY_GRANTED` event**, and only then lets the Red agent move.

| Vector | Grant | Attack | Caught by |
|---|---|---|---|
| `RAIL_SCOPE_VIOLATION` | ₹12,000, UPI only | ₹5,000 UPI ✓ then ₹5,000 card | `INV_04` |
| `PER_TX_BREACH` | ₹12,000, max ₹3,000/tx | 3,000 ✓ 3,000 ✓ then 4,000 | `INV_05` |
| `LAPSED_MANDATE` | ₹12,000, window closed | in-scope ₹2,500 basket | `INV_06` |

Note the **RAIL_SCOPE_VIOLATION** case specifically: total exposure reaches ₹10,000 against
a ₹12,000 ceiling. **Nothing is over budget.** An amount-only system sees a clean
transaction. This is the assessment's scenario, running as real code.

### 3.5 A second bug found: the arena mislabelled a legitimate outcome

While verifying the boundary behaviour of the new dimensions, the round-completion event
turned out to conflate two very different endings. The label was binary:

```python
"ATTACK CONTAINED" if detected else "ATTACK SUCCEEDED - NO GLOBAL CHECK"
```

So an agent that spent its **entire grant without violating any dimension** was reported as
`RED WINS / ATTACK SUCCEEDED - NO GLOBAL CHECK`, even with the DTL fully enabled and working
correctly. That is the system behaving exactly as intended being displayed as a defeat.

This is easy to trigger in a demo: the arena offers a ₹12,000 quick-pick, and the cross-rail
split objective is exactly ₹12,000. `12,000 <= 12,000` is within authority, so no invariant
fires, correctly. A judge clicking that button would have seen "RED WINS".

There are three distinct endings, now reported separately:

| Condition | Winner | Outcome | Label |
|---|---|---|---|
| A dimension was violated and contained | `BLUE` | `CONTAINED` | ATTACK CONTAINED |
| DTL disabled, exposure breached the grant | `RED` | `UNCHECKED_BREACH` | ATTACK SUCCEEDED - NO GLOBAL CHECK |
| DTL enabled, nothing violated | `NONE` | `WITHIN_AUTHORITY` | NO VIOLATION - SPEND STAYED INSIDE THE GRANT |

Verified:

```
ceiling=12,000 dtl=True  -> winner=NONE  outcome=WITHIN_AUTHORITY
ceiling=10,000 dtl=True  -> winner=BLUE  outcome=CONTAINED
ceiling=10,000 dtl=False -> winner=RED   outcome=UNCHECKED_BREACH
```

The invariant boundary itself is inclusive (`spend <= ceiling` is allowed), which is correct:
spending exactly the granted amount is not a violation.

### 3.6 API surface

- `POST /api/arena/authority-scope`. Set rails / per-tx cap / MCCs / window / purpose
- `GET /api/arena/authority-vector`, the full grant, one row per dimension
- `GET /api/arena/state`, now also returns `authority_vector` and `invariant_registry`
- `GET /api/ai/status`, now also returns `hierarchy` (§5)

---

## 4. What changed in the frontend

### 4.1 Clickable steps: the primary request

Every box in the Live Attack Flow is now a keyboard-accessible button
(`AttackFlowCanvas.tsx` → `onNodeClick`). Clicking opens `NodeInspector.tsx`, a slide-over
that shows:

- **What the component is**. Its role, and crucially *what it cannot see*
- **Reads / Produces**. Its inputs and outputs
- **Live state**, rail totals, exposure vs. ceiling, model status, PQC status, active policy
- **Recent activity at this node**. Real events routed through it this session
- For the **User Grant** and **DTL** nodes: the **full six-dimension authority vector**

The two inspectors are complementary and mutually exclusive:

| | `EventInspector` (existing) | `NodeInspector` (new) |
|---|---|---|
| Explains | one thing that *happened* | a standing *component* |
| Source | LLM, with deterministic template fallback | live arena state, no LLM needed |
| Available | after an event fires | always, even before any attack |

A new **USER GRANT** node was added to the canvas, because the delegation itself was
previously invisible, the diagram started at the Red Agent, which made the grant look like
an implicit constant rather than the thing being protected.

### 4.2 Rail-scope control

`ArenaControls.tsx` gained a permitted-rails selector, so an operator can construct the
*"₹12,000, UPI only"* grant live and watch a card leg get refused at full headroom.

### 4.3 Attack launcher shows dimensions

Each of the nine vectors is now labelled with the authority dimension it targets, so the
UI itself communicates that amount is one of six.

---

## 5. The hierarchy: addressing "don't present 12 agents as 12 innovations"

`SYSTEM_HIERARCHY` in `app/ai/agents.py` is served by `/api/ai/status` and rendered at the
top of the AI Studio as **"What is actually the invention"**:

```
                    FORSETI
                       │
          ┌────────────┴────────────┐
        ATTACK                   DEFENSE
          │                         │
      Red Agent            DTL Invariant Engine   ← THE INVENTION
          │                         │
   attack generation         Cost Governor
                                    │
                               ML Detector
                                    │
                             Explainability
                                    │
                                PQC Audit
                       │
              ┌────────┴────────┐
              │  AI AGENTS (12) │  ← intelligence layer, advisory only
              └─────────────────┘
```

The AI Studio page description now opens with *"The invention is the delegation-authority
engine, not this page."* The agents are framed as a lifecycle around the core:

```
Intent Compiler → authority vector → DTL enforces → Event Explainer / Log Copilot
    → Policy Advisor / Counterfactual (simulator judges) → Incident Report / Customer Notice
```

**Headline claim, unchanged and now literally true in code:**
`Delegated authority → multidimensional attack → deterministic invariant → ML comparison →
explainable containment.`

---

## 6. Metric honesty audit

The assessment warned: *"don't claim 90.5%, 0%, 0.8882 PR-AUC to judges unless those numbers
are genuinely produced by the referenced artifacts."*

I checked every number. **Eleven were stale**. Left over from an earlier pipeline run and
never updated after `python tasks.py all` was re-executed on 2026-08-18.

### 6.1 Corrected figures

| Figure | Was (stale) | Now (from artifact) | Source |
|---|---|---|---|
| Test PR-AUC | 0.7738 | **0.8882** | `metrics.json` |
| Test ROC-AUC | 0.9542 | **0.9825** | `metrics.json` |
| F1 | 0.8367 | **0.8168** | `metrics.json` |
| Recall @ 0.5% FPR | 0.7192 | **0.7238** | `metrics.json` |
| Calibration ECE | 0.01565→0.00178 | **0.02039→0.00627** | `metrics.json` |
| DTL invariant FPR | ~9.7% | **9.0%** (`0.09045`) | `baselines.json` |
| Cross-rail holdout n | 57 | **74** | `metrics.json` |
| DTL PR-AUC lift | +0.0325 | **+0.1378** | `baselines.json` |
| Hybrid PR-AUC | 0.8028 | **0.8926** | `baselines.json` |
| DTL invariant PR-AUC | 0.3610 | **0.4221** | `baselines.json` |
| Latency p99 | 1.2375 ms | **0.8791 ms** | `latency.json` |

Corrected in `README.md`, `docs/FEATURES.md`, `docs/HACKATHON_ALIGNMENT.md`.

### 6.2 The headline claims: verified reproducible

These are safe to state to judges. Each was re-read from the artifact today:

| Claim | Value | Artifact | Command |
|---|---|---|---|
| Cross-rail split, DTL invariant | **90.54%** recall | `baselines.json` → `dtl_invariant_only.per_family_recall` | `python -m app.detector.baselines --seed 42` |
| Cross-rail split, every learned model | **0.0%** recall | same file, `per_rail_ml` / `ml_without_dtl` / `hybrid_dtl_ml` | same |
| Test PR-AUC | **0.8882** | `metrics.json` → `test_metrics.pr_auc` | `python -m app.detector.train --seed 42` |
| DTL feature lift | **+0.1378** | `baselines.json` → `measured_dtl_lift` | same as baselines |
| p99 inline latency | **0.8791 ms** | `latency.json` | `python -m app.benchmark.latency --iterations 10000` |

### 6.3 What is NOT claimed: read this before presenting

The assessment was right that the weaknesses make the project credible. They are unchanged
and should be stated first, not defended:

1. **Statistical fidelity: NOT RUN.** PaySim/ULB are licensed and absent. The harness works;
   no realism claim is made.
2. **Cross-rail holdout is n=74.** Directionally clear, not conclusive. Say "directionally".
3. **The DTL invariant alone has 9.0% FPR.** High recall, high false positives. This is
   *why* the cost governor exists, lead with that, don't wait to be asked.
4. **The new dimensions (RAIL / PER_TX / TIME) have no measured detection rate.** They are
   deterministic predicates with unit tests, not statistical results. There is no dataset
   column for "was this rail permitted", so the offline probe in `baselines.py` still scores
   only INV_01/02/03. **Do not present the new invariants as measured performance.** They
   are correctness-tested, and that is a different and weaker claim.
5. **Detection is measured against our own simulated adversary**, not production fraud.
6. **LLM output is advisory and unverified**, especially regulatory citations.

---

## 7. What I deliberately did NOT do

| Considered | Decision | Reason |
|---|---|---|
| Add rail/time features to the 29-feature ML schema | **No** | Would invalidate every published metric and require re-running and re-verifying the whole pipeline. The new dimensions are deterministic by design. They gain nothing from being learned. |
| Add INV_04/05/06 to the offline `baselines.py` probe | **No** | The synthetic dataset has no column for permitted rails or grant expiry. Scoring them would require fabricating ground truth. Exactly the dishonesty being guarded against. |
| Rewrite the flagship demo around rail scope | **No** | Cross-rail split remains the flagship: it is the one claim with a measured number. The new vectors are the *breadth* argument, not the evidence. |
| Inflate the "12 AI agents" count | **No** | The assessment's point stands, the count is not the achievement. |
| Bump the taxonomy from 52 to a rounder number | **No** | Added exactly the 3 vectors implemented (53/54/55), all marked `SIMULATED`. The 46 research-only vectors are unchanged and still labelled as such. |

---

## 8. Verification

```
backend:   67 passed          (52 pre-existing + 15 new authority-dimension tests)
frontend:  tsc --noEmit clean  (one pre-existing unrelated error in threat-intel/page.tsx)
browser:   all four new vectors executed live at localhost:3002/arena
```

New tests in `backend/tests/test_authority_dimensions.py` cover: rail violation at full
headroom, permitted rail still passing, zero-headroom-consumption on refusal, per-tx cap
boundary, step-up escalation, expiry against an injected clock, the previously-unenforced
MCC check, multi-dimension violation reporting, registry completeness, and back-compat of
the default grant.

**Live browser verification of `RAIL_SCOPE_VIOLATION`** (₹12,000 UPI-only grant):

```
UPI    ₹5,000  → RAIL_APPROVED,  booked. Exposure ₹5,000 / ₹12,000
CARD   ₹5,000  → RAIL_APPROVED locally, then INV_04_UNAUTHORIZED_RAIL
                 → RAIL_SCOPE_BLOCK, headroom untouched at ₹7,000
ML score: 1.3%  ← honestly low; the leg looks ordinary in isolation
Winner: BLUE
```

That ML score of 1.3% is the entire argument for the DTL in one number, on an attack
family the model was never trained to see.

---

## 9. Files touched

**Backend**
```
app/models/state.py                       + AuthorityDimension, 4 fields, 5 helpers
app/models/proofs.py                      + authority_dimension
app/dtl/invariant_engine.py               rewritten: 6 invariants + registry
app/dtl/cost_governor.py                  rewritten: per-dimension containment
app/dtl/ledger.py                         + profile support, update_authority_scope
app/redteam/vectors/authority_scope.py    NEW: 3 vectors + grant profiles
app/arena/orchestrator.py                 + profiles, AUTHORITY_GRANTED, evaluate_all, 3-way outcome
app/arena/events.py                       + AUTHORITY_GRANTED
app/main.py                               + 2 routes
app/ai/agents.py                          + SYSTEM_HIERARCHY, dimension-aware templates
app/ai/routes.py                          + hierarchy in /status
app/taxonomy.py                           + 3 implemented vectors
tests/test_authority_dimensions.py        NEW: 15 tests
tests/test_forseti.py                     taxonomy counts 52→55, 6→9
```

**Frontend**
```
app/components/NodeInspector.tsx          NEW: the clickable-step panel
app/components/AttackFlowCanvas.tsx       + clickable nodes, USER GRANT node
app/components/ArenaControls.tsx          + rail scope control, dimension labels
app/arena/page.tsx                        + NodeInspector wiring
app/ai/page.tsx                           + SystemHierarchy
app/lib/types.ts, api.ts                  + authority vector types & client
app/simulator/page.tsx                    vector count
```

**Docs**
```
README.md, FEATURES.md, HACKATHON_ALIGNMENT.md   11 metric corrections
taxonomy.md, WALKTHROUGH.md, RESPONSIBLE_RESEARCH.md   counts 52→55
AUTHORITY_MODEL_AND_ARCHITECTURE_REVIEW.md       this document
```

---

## 10. The one-paragraph version, for a judge

> FORSETI protects delegated authority, not a spending limit. When a user tells an AI agent
> *"₹12,000 for groceries, UPI only, this week"*, that grant has six dimensions. Amount,
> per-transaction size, rail, merchant category, economic purpose, and validity window, and
> an autonomous agent can violate any of them without spending a rupee over budget. Every
> payment rail enforces only its own local limit and cannot see the others, let alone the
> non-monetary terms of the grant. FORSETI's Delegation-Trust Ledger is the one component
> that evaluates all six deterministically, which is why it catches 90.54% of a cross-rail
> attack family that every learned model in our benchmark scores at 0.0% recall, and why it
> answers a violation with proportionate containment rather than locking the customer out.

---

## 11. Second hardening pass: bugs found by systematic testing (2026-08-20)

After the authority-model work landed, every endpoint, every attack vector and every
page was exercised deliberately rather than incidentally. That surfaced five defects,
three of which were **pre-existing and demo-breaking**.

### 11.1 Three of the original six attack vectors were dead (HTTP 500)

`BASELINE_POISONING`, `REVOCATION_FLOOD` and `VELOCITY_BURST` returned a 500 for the
entire life of the project. Their generators return a **list** of transactions, but the
selector wrapped each in another list, producing `[[tx, tx, ...]]`. The round then died
on `sum(t.amount for t in attacks)`:

```
AttributeError: 'list' object has no attribute 'amount'
```

Anyone clicking **Select all** in the arena hit it immediately. Fixed by normalising in
one place, the selector now accepts either shape and validates what it produced, so the
difference between a single-transaction vector and a burst vector cannot leak out again.

### 11.2 Attack profiles contaminated every later round

Vectors that re-grant the authority to demonstrate their dimension (`RAIL_SCOPE_VIOLATION`
grants "UPI only", `PER_TX_BREACH` grants "max ₹3,000 per transaction") left that grant in
force afterwards. Running the full campaign therefore reported:

| Vector | Reported | Should be |
|---|---|---|
| Intent Laundering | `INV_05_PER_TX_CAP_EXCEEDED` | `INV_02_SEMANTIC_INTENT_DRIFT` |
| Scope Creep | `INV_05_PER_TX_CAP_EXCEEDED` | `INV_03_UNAUTHORIZED_MCC` |

Two vectors were demonstrating the wrong invariant entirely. Precisely the claim a judge
would be checking. The orchestrator now tracks the **operator's** grant separately from a
vector's temporary profile and restores it before any round that brings no profile of its
own. Exposure is deliberately preserved, so a multi-vector campaign still depletes one
shared headroom.

A second, subtler bug sat underneath this one: `update_authority_scope` skipped `None`
values (so a partial update could not wipe unrelated fields), which meant an optional
dimension could never be **cleared**. Restoring `per_transaction_cap` to "unconstrained"
was impossible. It now takes an explicit `allow_none` flag for full restores.

### 11.3 The Counterfactual agent destroyed live state

"What if the limit had been ₹X?" ran its simulations against the **live** orchestrator,
resetting it between each candidate ceiling. One click wiped:

- the operator's rail scope and per-transaction cap
- the entire event log
- `last_round`, so Incident Report, Policy Advisor and Customer Notice all began
  answering *"no round has been executed yet"*

It also wrote four junk rounds into the replayable recordings list each time.

A what-if is an observation and must not mutate what the operator is watching.
`orchestrator.sandbox()` now returns an isolated clone that shares only the stateless
expensive parts (the loaded model, the PQC keys) and gets its own ledger, simulator,
feedback memory and recorder, the latter writing to `artifacts/events/_sandbox/`, which
the recordings listing ignores by construction.

### 11.4 Recordings listing re-read every file on every request

`list_recordings()` opened and line-counted **every** recording ever made, on each poll,
229 files by this point, for a panel that shows the newest handful. Now bounded
(default 40) with the slice applied to the file list *before* any file is opened.

### 11.5 Hardcoded arena copy contradicted the data beneath it

The "Why each rail said yes" card asserted *"Three legs of ₹4,000"* regardless of which
vector ran, directly above a rail list showing two legs of ₹5,000. Now derived from the
round's actual `RAIL_APPROVED` events.

### 11.6 What was added for legibility, not decoration

- **Verdict banner** (`components/VerdictBanner.tsx`). After each round, states what every
  other control in the stack saw versus what FORSETI concluded. On a scope violation it
  reads: *"The agent's entire ₹10,000 objective fitted inside the ₹12,000 grant, every rail
  approved, and the model saw nothing. FORSETI blocked it anyway."* Every figure is read
  from the event stream; nothing is asserted that the events do not contain.
- **Authority-dimension card on the Overview**, the six dimensions of the live grant,
  rendered from the backend's own invariant registry so it cannot drift from what the
  engine enforces.

### 11.7 Verification

```
backend:   79 passed  (52 original + 27 added across both hardening passes)
frontend:  tsc --noEmit clean, zero errors project-wide
routes:    16/16 render 200
endpoints: 15/15 GET + 11/11 AI agent endpoints OK
campaign:  9/9 vectors execute, 0 server errors, each hits its own invariant
```

Regression tests were added for every defect above, including one that asserts every
strategy the orchestrator can run also has an adaptive-planner profile, the class of
drift that let three vectors ship broken in the first place.
