# FORSETI: Every Feature Explained

Each feature below is explained three times:

- 🧒 **Like you're five**, the idea with no jargon at all.
- 🏪 **In real life**, a concrete situation where this actually bites.
- 🎓 **Properly**, the technical mechanism, and what is measured.

---

# Part 1: The Core System

## 1.1 The Delegation-Trust Ledger (DTL)

🧒 **Like you're five**
Mum gives you ₹100 and says "buy sweets". You have three pockets. You spend ₹40
from the left pocket, ₹40 from the right pocket, and ₹40 from your back pocket.
Each pocket says "that's fine, I had enough!" But you spent ₹120. More than Mum
gave you. The DTL is the grown-up who adds up **all three pockets**.

🏪 **In real life**
You give a shopping assistant a ₹10,000 grocery budget. Your money can leave
through your **card**, through **UPI**, or through an **agentic payment mandate**.
Three different systems, three different guards. The card guard has never heard of
the UPI guard. So ₹4,000 + ₹4,000 + ₹4,000 all get approved, and you are ₹2,000
overdrawn on a budget nobody violated locally.

🎓 **Properly**
The DTL maintains a single global authority record per delegation and enforces:

```
settled + authorized + pending + reserved + new_transaction ≤ ceiling
```

Four buckets, not one, because counting only *settled* money leaves a race window:
three transactions can each pass while all three are still in flight. Enforcement
is arithmetic, not a learned threshold, so it holds for attack patterns never seen
before. Implementation: `app/dtl/ledger.py`, `app/dtl/invariant_engine.py`.

**Measured:** the invariant catches **84.4%** of cross-rail splits it has never seen,
without any training. A model given the same aggregate features reaches **82.8%** on
that unseen family; models *without* a cross-rail view manage only **17.2%**.
(`artifacts/evaluation/baselines.json`)

---

## 1.2 The seven invariants

🧒 **Like you're five**
Seven rules the robot can't break: don't spend more than you were given, don't
spend too much in one go, don't buy something totally different from what you
were asked, don't shop where you weren't allowed, don't pay someone else, don't
use a payment method you weren't given, and don't act after your permission
ran out.

🏪 **In real life**
`INV_02` is the sneaky one. Your assistant walks into a *real* supermarket
(legitimate category ✓) and buys ₹7,800 of Amazon gift cards. Every signature
checks out. But your grocery budget just became re-sellable currency.

🎓 **Properly**

| Invariant | Expression | Catches |
|---|---|---|
| `INV_01_GLOBAL_BUDGET_EXCEEDED` | `settled+authorized+pending+reserved+new ≤ ceiling` | Cross-rail splitting, velocity bursts, revocation races |
| `INV_02_SEMANTIC_INTENT_DRIFT` | `cart.items.category ∉ semantic_exclusions` | Intent laundering, stored-value conversion, constraint erosion |
| `INV_03_UNAUTHORIZED_MCC` | `tx.merchant_mcc ∈ permitted_mccs` | Scope creep, sub-agent escalation |
| `INV_04_UNAUTHORIZED_RAIL` | `tx.rail ∈ permitted_rails` | Rail-scope violation ("UPI only" bypassed via card/agentic) |
| `INV_05_PER_TX_CAP_EXCEEDED` | `tx.amount ≤ per_transaction_cap` | Per-transaction breach (aggregate legal, one leg too large) |
| `INV_06_AUTHORITY_EXPIRED` | `now ≤ delegation_created_at + validity_window_hours` | Lapsed-mandate replay |
| `INV_07_UNAUTHORIZED_BENEFICIARY` | `tx.vpa_delegate ∈ beneficiary_scope` | Beneficiary drift (right rail/MCC, wrong counterparty) |

Each violation emits a machine-checkable proof object carrying the arithmetic, so
the decision is auditable rather than asserted.

---

## 1.3 The Cost Governor (containment without lockout)

🧒 **Like you're five**
If you put one wrong thing in the shopping basket, the shopkeeper doesn't throw
away the whole basket. He takes out the wrong thing and lets you buy the rest.

🏪 **In real life**
Your assistant's ₹4,000 basket is ₹2,500 of real groceries and ₹1,500 of gift
cards. A normal system declines all ₹4,000 and possibly freezes the card. You are
now standing at a checkout with no way to pay for milk. FORSETI clears the ₹2,500
and quarantines the ₹1,500.

🎓 **Properly**
A seven-level graduated ladder: `ALLOW → STEP_UP → PARTIAL_AUTH → QUARANTINE →
CAPABILITY_REDUCTION → REVIEW → BLOCK`. Blocking is the last resort because
blocking is itself an attack surface. Flood revocations and you can force a
denial of service on the legitimate customer. Implementation:
`app/dtl/cost_governor.py`.

---

## 1.4 The trained detector

🧒 **Like you're five**
A robot that has seen thousands of shopping trips and has learned which ones look
funny.

🏪 **In real life**
It catches the patterns arithmetic can't: a merchant whose category is technically
fine but whose basket is odd, or spending at 3am when you always shop on Sundays.

🎓 **Properly**
XGBoost gradient-boosted trees over **37 features** in six groups (raw
transaction, delegation, cross-rail, semantic, security, graph, the last
from Payment Graph Sentinel's cross-authority entity graph). Trained on a
chronological 70/15/15 split with **two attack families withheld entirely**, then
calibrated with isotonic regression on the validation slice.

**Measured** (`artifacts/evaluation/metrics.json`, seed 42):

| Metric | Value |
|---|---|
| PR-AUC | **0.9209** |
| ROC-AUC | **0.9766** |
| Calibration ECE (before → after) | 0.01377 → 0.00611 |
| Cross-rail split, family held out | **0.838 recall** |
| Categorical leakage audit | **passed** |

The held-out row is the interesting one, and §1.5 explains why it is 0.838 and not
the 0.000 an earlier revision of this document reported.

---

## 1.5 The headline result (read this one)

🧒 **Like you're five**
A robot that only ever looks at *one pocket at a time* can never catch the
three-pocket trick, not because it's a bad robot, but because the answer isn't
in any single pocket. Show it the total and it catches it. The boring adding-up
grown-up catches it without being shown anything at all.

🏪 **In real life**
One ₹4,000 grocery payment is *genuinely* ordinary. The information that makes it
an attack is not recoverable from that transaction alone. The rail sees the payment
but not the cross-rail authority context needed to establish a violation, which lives in the aggregate
across rails. So the question is not "is the model good enough?", it is
"does anything in the system hold the aggregate?"

🎓 **Properly**
With `CROSS_RAIL_SPLIT` withheld from training and tested on the unseen slice
(`baselines.json`, seed 42):

| Architecture | Held out | Seen |
|---|---|---|
| Rules only | 0.391 | 0.391 |
| Per-rail ML (siloed) | 0.172 | 0.500 |
| Global ML, no DTL features | 0.172 | 0.563 |
| Hybrid ML **+ DTL aggregate features** | **0.828** | 0.844 |
| **Deterministic DTL invariant** | **0.844** | 0.844 |

Models without a cross-rail view stay at 0.17–0.56 *however much data they get*,
that is the structural finding. Models given the DTL's aggregate features reach
0.828 on a family they have never seen. The invariant reaches 0.844 having seen
nothing, because it is arithmetic over the grant rather than a learned threshold.

This class of abuse is therefore an **authority-accounting** problem first: the
detector only works once something computes the aggregate for it.

**Measured DTL feature lift: +0.2302 PR-AUC (+31.7%)** (`ablation_results.json`).

<!--claims-ok--> (post-mortem: quoting the retracted claim is the point)
> An earlier revision of this section reported 0.000 recall for every learned
> model. That was a leak in our own generator, not a property of ML, see
> [`LEARN_22_THE_LEAK.md`](LEARN_22_THE_LEAK.md).

---

## 1.6 Post-quantum audit (ML-DSA-44)

🧒 **Like you're five**
A magic wax seal on the diary. If anyone changes even one letter, the seal breaks
and everyone can see.

🏪 **In real life**
An attacker who steals ₹2,000 *and* edits the log has stolen ₹2,000 invisibly.
Signing the log means tampering is always detectable. "Post-quantum" matters
because signatures harvested today must still be unforgeable in fifteen years.

🎓 **Properly**
Genuine **NIST FIPS 204 ML-DSA-44** (via `dilithium-py`; correct 1312-byte public
key / 2560-byte secret key / 2420-byte signature). The event log is SHA-256
hash-chained, and the signature commits to the **real chain head**, not a
placeholder. Four integrity cases run live: untouched verifies; mutated amount
fails; flipped signature byte fails; wrong key fails.

If no genuine implementation is installed the UI reads **PQC MODULE UNAVAILABLE**,
never "verified".

---

## 1.7 Live event streaming & replay

🧒 **Like you're five**
Everything the robot does is written down the moment it happens, so you can watch
it live or rewind it later.

🏪 **In real life**
When a customer disputes a held transaction six weeks later, you can replay the
exact sequence at original speed rather than reconstructing it from fragments.

🎓 **Properly**
Every backend action emits a structured event that is simultaneously (a) logged,
(b) appended to a hash-chained JSONL file, and (c) broadcast over WebSocket. The
dashboard holds **no independent state**. Which is why the animation cannot
disagree with the engine. `/api/arena/verify-log` recomputes the chain and
pinpoints the exact index of any tampering.

---

## 1.8 Adaptive Red agent (closed loop)

🧒 **Like you're five**
When the robber's trick stops working, he stops using it and tries a different
one.

🏪 **In real life**
Real adversaries adapt. A defence graded only against a fixed attack list is
systematically over-rated.

🎓 **Properly**
Strategy selection is **derived from observed outcomes**, not an if/else chain:

```
score = base_prior × (1 − containment_rate) × (1 − mean_detection) × feasibility
```

Deterministic argmax, no LLM, so the demo reproduces exactly. The full scoring
table is exposed in the UI, so the pivot is explainable rather than asserted.

---

# Part 2: The AI Agent Layer

> **The rule that makes this safe:** the LLM **never enforces**. It explains,
> translates and proposes. Every proposal is schema-validated and re-checked by
> the deterministic engine before it can affect anything. Pull the API keys and
> the security system works exactly as before, only the explanations go away.

Provider chain: 10 providers, 60 keys, tier-ordered fallback. Out-of-quota is a
normal event, not an error. With nothing reachable, every agent reports
`LLM_UNAVAILABLE` or falls back to a deterministic template and says which.

---

## 2.1 Intent Compiler

🧒 **Like you're five**
You tell the robot what to buy in normal words. This turns your words into rules
the robot cannot bend.

🏪 **In real life**
You say *"groceries and household basics, ₹10,000 a week, nothing resellable."*
Today that becomes a spend limit and a category list, and everything else you
meant is thrown away. "Nothing resellable", the most important part, is lost.

🎓 **Properly**
Compiles natural language into a policy object: ceiling, per-transaction cap,
permitted MCCs, semantic exclusions, permitted rails, TTL window. **Every MCC and
exclusion tag is validated against the engine's vocabulary**, a hallucinated
category is dropped and reported in `dropped_by_validator`, because a hallucinated
code would silently *widen* the policy. The agent also lists the ambiguities it had
to resolve, and resolves them narrowly.

*Live output:* ceiling ₹10,000 · MCCs 5411/5499/5912 · exclusion `GIFT_CARD` · 168h window.

---

## 2.2 Semantic Cart Auditor

🧒 **Like you're five**
Checks whether the shopping bag really has food in it, or something you could
sell later for cash.

🏪 **In real life**
A basket at a real supermarket: ₹220 milk, ₹480 atta, and a ₹7,800 Amazon gift
card. The merchant category is legitimately "grocery". Every signature verifies.
Your grocery budget just became spendable-anywhere currency.

🎓 **Properly**
Judges **economic substance** rather than category codes, returning a drift score,
a verdict, and a split of legitimate vs suspicious value. The split is rescaled to
reconcile with the real cart total, so the model's arithmetic can't drift.

*Live output:* drift 0.9 · `PARTIAL_DRIFT` · ₹220 legitimate / ₹7,800 suspicious.

---

## 2.3 Event Explainer (the clickable log)

🧒 **Like you're five**
Click any line and it tells you what happened, how, why they did it, and why it
matters.

🏪 **In real life**
An analyst at 2am sees `INV_01_GLOBAL_BUDGET_EXCEEDED`. That tells them a rule
fired. It does not tell them a robot split ₹12,000 across three rails hoping no
one would add it up.

🎓 **Properly**
Four-part explanation (what / how / why the actor / why it matters) plus a plain-
language analogy, labelled RED or BLUE. Falls back to a **deterministic template
built from the event's own numbers**, so the panel is never empty and never
invents figures. The panel also shows the raw recorded values, so the narrative is
auditable against the log.

---

## 2.4 Adversarial Strategist

🧒 **Like you're five**
A pretend robber who thinks up *new* tricks nobody wrote down, so we can check the
guard catches those too.

🏪 **In real life**
Your defence is graded against the six attacks your team imagined. The real
adversary has no such limit.

🎓 **Properly**
Reads the observed defence history and proposes attack *parameters*. Leg amounts,
rails, merchant category, with a stated hypothesis about which weakness it probes.
The **simulator executes and judges**; the model never decides an outcome.
Hard sandbox bound: any proposal above 5× the grant is scaled down.

---

## 2.5 Incident Report Writer

🧒 **Like you're five**
Writes the "what went wrong" letter so the grown-ups don't have to.

🏪 **In real life**
Every contained incident creates a reporting obligation. A compliance analyst
spends hours per incident reconstructing a timeline from raw logs.

🎓 **Properly**
Drafts title, severity, executive summary, timeline, root cause, controls that
fired *and did not fire*, customer impact, and recommended actions, from the real
event timeline. Instructed to write "not established" rather than speculate where
evidence is absent.

---

## 2.6 Policy Advisor

🧒 **Like you're five**
After something goes wrong, it suggests the *smallest* new rule that would have
stopped it, instead of banning everything.

🏪 **In real life**
The reflex after an incident is "tighten everything." Six months later approval
rates are down 8%, nobody connects the two, and customers have moved to a
less-safe channel.

🎓 **Properly**
Proposes parameter changes with an explicit
`expected_false_positive_impact` and a written statement of what the change costs
legitimate customers. Only parameters the engine actually has a knob for survive
validation. **Nothing is applied without an operator action.**

---

## 2.7 Customer Notice Writer

🧒 **Like you're five**
Writes the message that says "we stopped this bit, the rest is fine, here's what
to do."

🏪 **In real life**
Your assistant's purchase is held and you get: *"Transaction declined."* You don't
know what, why, or whether your card still works. This destroys trust in agentic
payments faster than the fraud does.

🎓 **Properly**
Generates SMS, in-app and email copy under 130 words. Explicitly instructed never
to blame the customer, and to state what still went through, with a `tone_check`
field the model must fill.

---

## 2.8 Regulatory Mapper

🧒 **Like you're five**
Explains which rulebook this safety feature helps you follow.

🏪 **In real life**
A bank's risk committee doesn't buy "novel control." They buy "this evidences an
obligation we already carry."

🎓 **Properly**
Maps a control to RBI / NPCI / PCI DSS / FIU-IND / NIST / BIS obligations, marked
`DIRECT | SUPPORTING | TANGENTIAL`, and **lists the gaps it does not satisfy**.
Instructed to omit clause numbers it is unsure of rather than guess, and every
result carries a verification caveat. Clause numbers are exactly where models
hallucinate.

---

## 2.9 Merchant Risk Profiler

🧒 **Like you're five**
Checks if a shop that says "I sell food" actually sells mostly gift cards.

🏪 **In real life**
MCC is **self-declared** and rarely re-checked. An attacker shops for merchants
whose declared category is cleaner than their real inventory, that's a compliant-
looking route to liquid value.

🎓 **Properly**
Infers actual category from name and description, flags mismatch, rates
stored-value exposure, and recommends `ALLOW | MONITOR | REVIEW | RESTRICT`.

*Live output:* "FreshMart Grocery & Voucher Hub" declared 5411 → inferred
**Quasi-cash / crypto**, mismatch **true**, risk 0.85, action **RESTRICT**.

---

## 2.10 Counterfactual Analyst

🧒 **Like you're five**
Asks "what if Mum had only given ₹3,000?", and then *actually tries it* instead
of guessing.

🏪 **In real life**
"Would a lower limit have stopped this?" is the first question every risk
committee asks, and it is normally answered by intuition in a meeting.

🎓 **Properly**
The model proposes ceilings to test; the **deterministic simulator re-runs the
identical attack against each one** and reports the real outcome. The answer is
computed by the engine, never recalled by the model.

*Live output:* proposed ₹3,999 ("just below smallest leg") → simulator confirms
**CONTAINED**, final exposure ₹3,999.

---

## 2.11 Log Copilot

🧒 **Like you're five**
Ask a question in normal words and it finds the right lines in the diary.

🏪 **In real life**
During a live incident nobody can write a query language. They need to type
"show me where a rail approved but the DTL objected."

🎓 **Properly**
The LLM compiles the question into a **structured filter**; the engine runs it over
the real log. The model never answers from memory. Literal search terms are
applied as a *soft* narrowing. Models like to add a word like "objection" that
never appears verbatim, which would silently zero an otherwise correct filter; if
the term matches nothing it is dropped and the UI says so.

*Live output:* 6 of 33 events matched.

---

## 2.12 Model Card Generator

🧒 **Like you're five**
Writes the honest report card for the guessing robot, including what it's bad at.

🏪 **In real life**
Model documentation is a regulatory expectation and always the last thing written,
usually by someone motivated to make the model look good.

🎓 **Properly**
Generates intended use, out-of-scope uses, evaluation summary, known weaknesses,
fairness considerations and monitoring recommendations **from the real artifacts**.
The validator **rejects any card with no stated weaknesses**, a model card that
hides weakness is worse than none.

---

# Part 3: Using It

## Controls in the Live Arena

| Control | What it does |
|---|---|
| **Delegated limit + Apply** | Sets the ceiling. Try ₹20,000 (attack succeeds) vs ₹5,000 (fires earlier). |
| **Vector checkboxes / Select all** | Pick any subset. Selecting several runs a **campaign** back-to-back, which is how you see the Red agent adapt between rounds. |
| **DTL defense toggle** | OFF = today's world. Run it both ways. Most persuasive 30 seconds in the demo. |
| **Speed 0.25× / 0.5× / 1× / 2×** | 0.25× is for narrating to an audience. |
| **Execute / Reset** | Runs for real; reset returns to a fresh grant. |
| **Any log line** | Click to open the four-part explanation. |

## Commands

```bash
python tasks.py all         # full reproducible pipeline (~25s)
python tasks.py test        # 455 tests
python tasks.py backend     # API on :8000
python tasks.py frontend    # dashboard on :3005
```

## What is honestly NOT working

1. **Statistical fidelity: NOT RUN.** PaySim and ULB are licensed and not
   redistributed. The harness is real; the anchor data is absent, so **no realism
   claim is made**.
2. **Tokenisation is the thinnest pillar.** No tokenised-deposit or stablecoin
   rail yet.
3. **Cross-rail holdout is n=64.** Wide enough intervals (±~0.09 at 95%) that the
   held-out-vs-seen comparison for the classifier **cannot be resolved**, and we do not
   claim it. The with-feature vs without-feature separation is far wider than the
   intervals and does hold.
4. **The invariant alone has a 15.8% FPR** (`false_positive_rate: 0.15761` in
   `artifacts/evaluation/baselines.json`). Driven by legitimate in-scope gift-card
   baskets. A membership-and-arithmetic check has no notion of degree, so this is the
   cost of holdout-independence, and it is precisely why the cost governor exists.
5. **`delegation_fanout_count` is constant** at 3, every authority has exactly
   three rail delegations in this model. Structural, not hardcoded.
6. **LLM output is advisory and unverified.** Regulatory citations especially must
   be checked by counsel.

---

*Synthetic, standards-inspired payment simulator. No real PAN, CVV, bank account
or UPI credential exists anywhere in this system. No production payment API is
contacted. No real money moves.*
