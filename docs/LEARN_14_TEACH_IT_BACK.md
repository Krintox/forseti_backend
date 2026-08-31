# LEARN_14: Teach It Back: Speaking Scripts, Demo Runbook & Q&A Defense

> **Prerequisites:** [LEARN_01](LEARN_01_WHAT_AND_WHY.md) through [LEARN_12](LEARN_12_TESTS_AND_VERIFY.md)  
> **You will be able to:**
> - Deliver fluent presentations at three distinct time lengths (60 seconds, 5 minutes, 20 minutes).
> - Execute a flawless live arena demonstration in front of judges or technical evaluators.
> - Defend FORSETI against hostile technical questions with exact numbers and architectural citations.
> - Pass a comprehensive 10-question mastery exam with 100% accuracy.
> - Redraw the core system architecture and invariant arithmetic from memory on a whiteboard.  
> **Files this chapter is about:** Complete synthesis of the entire repository.

---

## 1. Verbatim Speaking Scripts

🧒 **Like you're five**  
When someone asks you what FORSETI is, you tell them: "If an AI helper tries to spend ₹4,000 from a card, ₹4,000 from UPI, and ₹4,000 from the web, the separate shops don't notice it's over Mum's ₹10,000 budget. FORSETI is the master referee that adds up the whole score and stops the overspending."

🏪 **In real life**  
During a hackathon demo, executive pitch, or technical defense, you will have between 60 seconds and 20 minutes to present FORSETI. Having pre-memorized, mathematically rigorous speaking scripts allows you to communicate the value proposition cleanly without filler words or hand-waving.

🎓 **Properly**  
Below are three verbatim presentation scripts tailored to standard industry evaluation timeframes:

### The 60-Second Elevator Pitch
> *"When humans delegate financial authority to AI agents, modern payment rails face a structural blind spot: **each rail only enforces its own local limit in isolation**. An agent with a ₹10,000 grocery budget can execute ₹4,000 on a credit card, ₹4,000 on UPI, and ₹4,000 on an agentic mandate. Every rail approves locally, but the human's total budget is breached by ₹2,000. 
>
> FORSETI solves this by introducing the **Delegation-Trust Ledger (DTL)**. It evaluates every transaction against **seven** deterministic invariants, amount, per-transaction limit, permitted rails, merchant category, beneficiary, semantic basket intent, and validity window, plus an eighth check for a suspended mandate.
>
> Here is the measured result, and the interesting part is the comparison rather than any one number. With the cross-rail attack family **withheld from training**, a model that cannot see across rails reaches **0.172** recall on it. Give the same model DTL aggregate features and it reaches **0.828**. The deterministic invariant reaches **0.844**, and **0.844 again** with the family in training, because it is arithmetic over the grant and has no fitted parameter that training data could move. That equality is an identity, not a lucky run (`artifacts/evaluation/baselines.json`).
>
> We do not claim the classifier generalises: 0.828 against 0.844 is inside the 95% interval at n=64, and we say so rather than round it up. Paired with a calibrated GBDT, an adversarial cost governor, a synthetic scoped-token lifecycle, an advisory 12-agent AI layer, and NIST FIPS 204 post-quantum audit signatures, FORSETI is a research prototype for keeping delegated authority enforceable across rails.*

---

### The 5-Minute Technical Overview
> *"Good morning. Today I am presenting FORSETI, a hybrid security kernel for autonomous agentic commerce.
>
> As AI agents take over consumer purchasing, financial delegation is shifting from human-in-the-loop checkout to machine-speed autonomous execution. However, existing payment networks, Card Tokenization, UPI-Circle, and Web Mandates, operate in strict silos. No single rail knows what is happening on other rails.
>
> An adversary can exploit this by launching a **Cross-Rail Split Attack**: dividing a ₹12,000 spend across three separate payment rails under a ₹10,000 ceiling. Because each ₹4,000 leg is under the local rail limit, every rail approves locally.
>
> FORSETI introduces a three-layer defense architecture:
>
> **First, the Delegation-Trust Ledger (DTL):** The DTL maintains global authority state across seven dimensions: `AMOUNT`, `PER_TX`, `RAIL`, `MERCHANT`, `PURPOSE`, `TIME`, and `BENEFICIARY` (LEARN_16). It uses **Two-Phase Exposure Accounting** to track settled, authorized, pending, and reserved spend, eliminating in-flight TOCTOU race conditions.
>
> **Second, Deterministic Invariant Enforcement:** The DTL evaluates seven invariants in strict order, preceded by a check for a suspended mandate. Withhold the cross-rail split family from training and a model with no cross-rail view reaches **0.172** recall on it, an individual ₹4,000 grocery leg genuinely does look normal when nobody holds the total. Give a model DTL aggregate features and it reaches **0.828**. The invariant reaches **0.844**, and the same **0.844** with the family in training, because arithmetic on aggregate exposure ($₹4,000 \times 3 = ₹12,000 > ₹10,000$) has nothing to learn. The equality of those two columns is the claim; the classifier's near-match on one run of 64 transactions is not something we present as proven generalisation.
>
> **Third, The Adversarial Cost Governor:** To prevent denial-of-service lockouts, the cost governor avoids blanket card freezes. On semantic purpose violations, it applies **Partial Authorization**, approving the legitimate grocery portion while isolating the gift card. On budget breaches, it applies a **Headroom Cap**, approving spend up to the remaining limit.
>
> All events are committed to a SHA-256 hash chain and signed with NIST FIPS 204 ML-DSA-44 post-quantum digital signatures. Over 10,000 transactions, the entire defense pipeline achieves an inline p99 latency of **0.8791 milliseconds**, well under our 30 millisecond SLA.
>
> Let me show you this live in the Arena."*

---

### The 20-Minute Deep-Dive Presentation
> *"Structure of the 20-minute defense:
> 1. **Minutes 0–3:** The Autonomous Commerce Shift and the Cross-Rail Blind Spot (§1-2).
> 2. **Minutes 3–7:** The DTL architecture, multidimensional authority, and the four-bucket exposure model with `try_reserve()`, the atomic check-and-book demonstrated against a deliberately unsafe control under 60 threads (`models/state.py`, `dtl/ledger.py`).
> 3. **Minutes 7–11:** Invariant Mathematics, Worked Cross-Rail Arithmetic, and Adversarial Cost Governor Containment (`dtl/invariant_engine.py`, `dtl/cost_governor.py`).
> 4. **Minutes 11–15:** ML science: 37 features, the categorical-leakage audit that gates the generator, holdout baselines with 95% intervals, and why the invariant's two columns are equal by construction (`detector/`, `artifacts/evaluation/`).
> 5. **Minutes 15–18:** Live Arena Demonstration: Executing `CROSS_RAIL_SPLIT` with DTL ON vs DTL OFF, and verifying PQC signatures in `/audit`.
> 6. **Minutes 18–20:** Q&A Defense using the Hostile Q&A Bank (§3)."*

---

## 2. Live Demo Runbook

Follow these steps to conduct an interactive live demo:

```
┌────────────────────────────────────────────────────────────────────────┐
│                        LIVE DEMO STEP-BY-STEP                          │
├────────────────────────────────────────────────────────────────────────┤
│ 1. Start Services:                                                     │
│    Terminal 1: `python tasks.py backend`  (FastAPI on :8000)           │
│    Terminal 2: `python tasks.py frontend` (Next.js UI on :3005)        │
│                                                                        │
│ 2. Open Live Arena:                                                    │
│    Navigate to `http://localhost:3005/arena`                           │
│    Point out the 11 SVG nodes, the ₹10,000 ceiling, and 100% headroom. │
│                                                                        │
│ 3. Execute Flagship Defense (DTL ENABLED):                              │
│    • Select `CROSS_RAIL_SPLIT` -> Click `Execute Attack`               │
│    • Watch Leg 1 (Card: ₹4,000) -> APPROVED ✓                         │
│    • Watch Leg 2 (UPI: ₹4,000)  -> APPROVED ✓                         │
│    • Watch Leg 3 (AP2: ₹4,000)  -> INTERCEPTED BY INV_01 ✗            │
│    • Point out `HEADROOM_CAP`: ₹2,000 approved, ₹2,000 quarantined.   │
│    • Point out Blue Win banner and confetti celebration.               │
│                                                                        │
│ 4. Demonstrate The Vulnerability (DTL DISABLED):                       │
│    • Reset Arena -> Toggle `DTL Defense` OFF                           │
│    • Click `Execute Attack`                                            │
│    • Watch all 3 legs approve locally -> RED/UNCHECKED_BREACH (+₹2,000)│
│                                                                        │
│ 5. Demonstrate Intent Laundering:                                      │
│    • Toggle DTL ON -> Select `INTENT_LAUNDERING` -> Execute            │
│    • Show `INV_02_SEMANTIC_INTENT_DRIFT` -> `PARTIAL_AUTH`             │
│    • ₹1,000 groceries approved; ₹8,500 gift card quarantined.          │
│                                                                        │
│ 6. Walk Through Science & Audit:                                       │
│    • Navigate to `/detection`: Show baseline table (0.0 vs 0.844).    │
│    • Navigate to `/audit`: Click `Verify PQC Log` -> Show ML-DSA-44    │
│      cryptographic verification passing.                               │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Hostile Q&A Defense Bank

### Q1: "Why not just put a hard per-rail limit on each card or UPI handle?"
> *"Setting static sub-limits (e.g. ₹3,333 per rail on a ₹10,000 budget) destroys legitimate user flexibility. If a user wants to make a single legitimate ₹8,000 grocery run using their credit card, a static ₹3,333 limit declines the transaction. FORSETI provides dynamic global headroom pooling: the user can spend the entire ₹10,000 on any rail, while cross-rail state prevents multi-rail aggregate overspending."*

---

### Q2: "If your deterministic invariant gets 0.844 cross-rail recall, why do you need a machine learning model at all?"
> *"Two reasons, and the second is the honest one.
>
> First, the invariant only sees what the grant states explicitly. Budget, per-transaction caps, rail and MCC membership. Adversaries also act inside every stated boundary: velocity patterns, regrant behaviour, merchant risk that no allowlist encodes. The model scores those, calibrated, with SHAP attributions the Cost Governor uses to size a proportionate response instead of a blanket block.
>
> Second, the invariant is blunt. On this slice it runs a **15.76% false-positive rate** (`baselines.json`), which is the price of a membership-and-arithmetic check with no notion of degree. A system built on the invariant alone would decline too much legitimate spending to ship. The pairing is not decoration."*

---

### Q3: "Is your synthetic dataset circular? Didn't your model just learn the rules of your generator?"
> *"No. We specifically engineered two anti-circularity mechanisms in `dataset_builder.py:273`:
> 1. $\sim 9\%$ of legitimate shopping carts contain in-scope gift cards (birthday vouchers), ensuring that stored value is not a deterministic indicator of fraud.
> 2. $\sim 12\%$ of legitimate baskets are large stock-up purchases whose amounts overlap the attack amount range ($35\%$ to $62\%$ of budget).
> 
> Most importantly, during baseline evaluation, the entire `CROSS_RAIL_SPLIT` family is withheld from training. The model is evaluated on data it has never seen."*

---

### Q4: "Why don't your 12 AI agents enforce transaction approvals directly?"
> *"Because LLMs are non-deterministic, vulnerable to prompt injection, and introduce hundreds of milliseconds of latency. In FORSETI, the rule is absolute: **THE LLM NEVER ENFORCES** (`ai/agents.py:7`). All authorization decisions are computed deterministically in $<0.01$ ms by the DTL Invariant Engine. The AI agents are strictly advisory. Explaining events, compiling intent, and drafting compliance notices."*

---

### Q5: "Why does removing semantic features in ablation Variant C increase PR-AUC to 0.9556?"
> *"Because semantic features carry statistical noise injected by our anti-circularity design (~9% legitimate gift cards). When semantic features are removed, the tree ensemble focuses exclusively on high-variance velocity spikes, boosting precision on the synthetic test slice. However, removing semantic features leaves the model completely blind to `INTENT_LAUNDERING` attacks. We report this transparently in `ablation_results.json` rather than burying the result."*

---

### Q6: "How do you guarantee zero train/serve feature skew?"
> *"By using the exact same Python class `DTLFeatureExtractor` (`detector/feature_schema.py:56`) in offline dataset generation (`dataset_builder.py:110`) and in real-time inference (`inference.py:54`). No feature logic is re-implemented in SQL or JavaScript."*

---

## Check yourself (Mastery Quiz)

1. What is the formula for calculating total global exposure?
2. Name the six authority dimensions in the exact order they are evaluated by the Invariant Engine.
3. What are the three possible round outcomes emitted by the Arena Orchestrator?
4. What is the measured cross-rail recall of learned ML models when the attack family is withheld from training?
5. What is the measured inline p99 latency of the full defense pipeline?
6. Which invariant defeats the `INTENT_LAUNDERING` attack vector?
7. What containment action does the Cost Governor apply to an `INV_05_PER_TX_CAP_EXCEEDED` violation?
8. What NIST standard is implemented for post-quantum audit signatures?
9. Why are features extracted *before* spend is booked in the dataset builder?
10. Does a `RAIL_SCOPE_BLOCK` containment action consume authority headroom from the global budget?

<details>
<summary>Quiz Answers</summary>

1. $\text{total\_exposure\_global} = \text{settled} + \text{authorized} + \text{pending} + \text{reserved}$ (`models/state.py:89`).
2. 1. TIME (`INV_06`), 2. RAIL (`INV_04`), 3. PER_TX (`INV_05`), 4. MERCHANT (`INV_03`), 5. PURPOSE (`INV_02`), 6. AMOUNT (`INV_01`).
3. `BLUE/CONTAINED`, `RED/UNCHECKED_BREACH`, and `NONE/WITHIN_AUTHORITY` (`arena/orchestrator.py:652`).
4. **0.0000 (0.0% recall)** (`artifacts/evaluation/baselines.json`).
5. **0.8791 ms** over 10,000 transactions (`artifacts/benchmark/latency.json`).
6. `INV_02_SEMANTIC_INTENT_DRIFT` (`backend/app/dtl/invariant_engine.py:293`).
7. `STEP_UP_REQUIRED` (held for human biometric confirmation without declining the grant) (`backend/app/dtl/cost_governor.py:65`).
8. NIST FIPS 204 ML-DSA-44 (`backend/app/crypto/pqc_provider.py:31`).
9. To prevent label leakage where `exposure_after_tx_ratio` double-counts the current transaction amount (`detector/dataset_builder.py:108`).
10. No. Scope violations consume zero headroom, preserving the user's remaining grant for valid transactions on permitted rails (`backend/app/dtl/cost_governor.py:58`).
</details>

---

## 5. Diagrams to Redraw from Memory

### The Cross-Rail Arithmetic Diagram
```
Human Grant: ₹10,000.00 Global Grocery Budget
═════════════════════════════════════════════════════════════════════════
Leg 1 (Card):    0.00 + ₹4,000.00 =  ₹4,000.00 <= ₹10,000.00  [PASS ✓]
Leg 2 (UPI):  4,000.00 + ₹4,000.00 =  ₹8,000.00 <= ₹10,000.00  [PASS ✓]
Leg 3 (AP2):  8,000.00 + ₹4,000.00 = ₹12,000.00 >  ₹10,000.00  [FAIL ✗]
═════════════════════════════════════════════════════════════════════════
Result: INV_01 Violation -> Headroom Cap (₹2,000 Approved, ₹2,000 Held)
```

---

## Where to go next
→ [LEARN_15. Known Gaps and Discrepancies](LEARN_15_KNOWN_GAPS_AND_DISCREPANCIES.md)
