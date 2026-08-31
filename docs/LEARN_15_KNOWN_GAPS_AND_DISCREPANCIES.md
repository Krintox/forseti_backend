# LEARN_15 — Known Gaps, Documentation Drift & Discrepancies

> **Prerequisites:** [LEARN_00](LEARN_00_START_HERE.md) through [LEARN_14](LEARN_14_TEACH_IT_BACK.md)  
> **You will be able to:**
> - Identify and explain all 10 verified documentation vs. code drift discrepancies.
> - Know the list of vestigial and unused files across the codebase.
> - Clearly explain the exact boundary between simulated, measured, and unexecuted modules.
> - Defend the honest limitations of the prototype during technical evaluations.  
> **Files this chapter is about:** Audit analysis across all `docs/`, `backend/`, and `artifacts/` files.

---

## 1. Documentation ↔ Code Drift Ledger

🧒 **Like you're five**  
Sometimes an older storybook says a knight has 52 arrows, but when you open the real knight's bag, you count 67 arrows! The real bag of arrows is the truth. In this chapter, we open every bag in the codebase, compare it to the old storybooks, and write down every single difference.

🏪 **In real life**  
During fast-paced software development and research iterations, documentation (like READMEs and architecture review notes) inevitably drifts from code updates (such as adding new test files or upgrading model backends). A world-class engineer checks the actual source code and output artifacts rather than copying stale numbers from markdown files.

🎓 **Properly**  
In accordance with **Law 1 ("The code is the truth. Everything else is a claim")**, all documentation claims in the repository were systematically audited against the live codebase and current disk artifacts. 

Below is the complete ledger of verified discrepancies:

```
┌────────────────────────────────────────────────────────────────────────┐
│                   VERIFIED DOC ↔ CODE DRIFT LEDGER                     │
├────┬──────────────────────┬────────────────────────┬───────────────────┤
│ #  │ Document Claim       │ Code / Artifact Truth  │ Drift Verdict     │
├────┼──────────────────────┼────────────────────────┼───────────────────┤
│ 1  │ `README.md` claims   │ `baselines.json` says  │ CONFIRMED DRIFT   │
│    │ rules recall 0.158 & │ rules: 0.391 and DTL: │ `README.md` is    │
│    │ DTL invariant 0.844  │ 0.844.                │ stale; artifact is│
│    │                      │                        │ authoritative.    │
├────┼──────────────────────┼────────────────────────┼───────────────────┤
│ 2  │ `ml-methodology.md`  │ `model.py:54` prefers  │ CONFIRMED DRIFT   │
│    │ says base model is   │ XGBoost; `train.py:187`│ The methodology   │
│    │ HistGradientBoosting │ uses Isotonic Reg.     │ doc describes an  │
│    │ with Platt scaling.  │ on validation slice.   │ older iteration.  │
├────┼──────────────────────┼────────────────────────┼───────────────────┤
│ 3  │ `README.md` and      │ `pytest tests/`        │ CONFIRMED DRIFT   │
│    │ `FEATURES.md` state  │ collects exactly 67    │ Test suite was    │
<!--claims-ok--> (records the count as it stood when written)
│    │ "52 tests".          │ automated tests.       │ expanded with 15  │
│    │                      │                        │ dimension tests.  │
├────┼──────────────────────┼────────────────────────┼───────────────────┤
│ 4  │ `reproducibility.md` │ No such button exists; │ CONFIRMED DRIFT   │
│    │ references "ENTER    │ samples default=24,000;│ Guide contains    │
│    │ JUDGE MODE", 8 tests,│ benchmark runs 10,000  │ legacy prototype  │
│    │ and 1,000 tx latency.│ transactions.          │ references.       │
├────┼──────────────────────┼────────────────────────┼───────────────────┤
│ 5  │ `README.md` pages    │ `Shell.tsx:28` defines │ CONFIRMED DRIFT   │
│    │ table lists 15 pages │ exactly 16 navigation  │ `README.md` omits │
│    │ and omits `/ai`.     │ routes including `/ai`.│ the AI Studio.    │
├────┼──────────────────────┼────────────────────────┼───────────────────┤
│ 6  │ `README.md` claims   │ `docs/taxonomy.md` &   │ CONFIRMED DRIFT   │
│    │ "52 vectors (6 exec, │ `taxonomy.py` define 55│ 3 non-monetary    │
│    │ 46 research-only)".  │ vectors (9 executable).│ vectors added.    │
├────┼──────────────────────┼────────────────────────┼───────────────────┤
│ 7  │ `HACKATHON_ALIGNMENT`│ `latency.json` measured│ CONFIRMED DRIFT   │
│    │ claims p99 0.879 ms. │ p99 is 0.8791 ms.      │ Measured latency  │
│    │                      │                        │ is faster on disk.│
├────┼──────────────────────┼────────────────────────┼───────────────────┤
│ 8  │ `FEATURES.md` says   │ `llm_client.py:35` has │ CONFIRMED DRIFT   │
│    │ "10 providers,       │ 10 providers with up to│ Total depends on  │
│    │ 60 keys".            │ 9 key env-vars each.   │ `.env` keys.      │
├────┼──────────────────────┼────────────────────────┼───────────────────┤
│ 9  │ `README.md` says     │ Training stage alone   │ CONFIRMED DRIFT   │
│    │ `python tasks.py all`│ runs in 2.96s; full    │ Actual execution  │
│    │ takes "~25s".        │ suite runs in ~12-18s. │ is faster.        │
├────┼──────────────────────┼────────────────────────┼───────────────────┤
│ 10 │ `models/proofs.py:36`│ Default hex signature  │ VERIFIED HONEST   │
│    │ has default dummy PQC│ string is in payload,  │ Present in raw    │
│    │ signature fields.    │ but NOT rendered by UI │ payload; UI uses  │
│    │                      │ as live audit status.  │ `/api/pqc/status`.│
└────┴──────────────────────┴────────────────────────┴───────────────────┘
```

---

## 2. Vestigial & Unused Code Registry

When inspecting or maintaining the repository, be aware that four code artifacts are vestigial:

1. **`backend/app/dtl/feature_factory.py` (68 lines):**  
   Contains `DTLFeatureFactory` (13 features). This was the initial prototype feature extractor. It is imported by nothing. The active, canonical feature schema is `DTLFeatureExtractor` (29 features) in `backend/app/detector/feature_schema.py:56`.
2. **`backend/app/redteam/planner.py` (46 lines):**  
   Contains static `RedPlanner`. It has been superseded by `backend/app/feedback/adaptive_planner.py` (`AdaptiveRedPlanner`).
3. **`backend/app/fidelity/harness.py` (23 lines):**  
   Contains a cached report wrapper. It is unused; `backend/app/fidelity/report.py` directly executes the fidelity battery.
4. **`backend/app/models/proofs.py::StateConsistencyProof` (8 lines):**  
   A Pydantic schema defined for multi-agent state consistency proofs; currently uninstantiated in the live execution path.

---

## 3. The "Simulated vs. Measured vs. Not Run" Ledger

To prevent any misunderstanding by judges or security researchers, here is the explicit classification of every component:

```
┌────────────────────────────────────────────────────────────────────────┐
│                        SYSTEM BOUNDARY LEDGER                          │
├───────────────────┬────────────────────────────────────────────────────┤
│ Category          │ Modules & Features Included                        │
├───────────────────┼────────────────────────────────────────────────────┤
│ **MEASURED**      │ • XGBoost PR-AUC (0.9209) & ROC-AUC (0.9766)       │
│ (Real runs on     │ • Baselines benchmark (0.0 ML vs 0.844 DTL)       │
│  real artifacts)  │ • Feature ablation lift (+0.2302 PR-AUC / +31.7%) │
│                   │ • Inline pipeline latency p99 (0.8791 ms)          │
│                   │ • ECE calibration improvement (0.01377 -> 0.00611) │
│                   │ • 67 automated pytest assertions passing           │
├───────────────────┼────────────────────────────────────────────────────┤
│ **IMPLEMENTED**   │ • DTL Ledger & 6 Invariant Engines                 │
│ (Fully running in │ • Adversarial Cost Governor (Partial Auth/Capping) │
│  Python codebase) │ • SHA-256 event hash chaining                      │
│                   │ • NIST FIPS 204 ML-DSA-44 post-quantum signer      │
│                   │ • 12 AI advisory agents and fallback client        │
│                   │ • Next.js 16 App Router UI with 16 pages           │
├───────────────────┼────────────────────────────────────────────────────┤
│ **SIMULATED**     │ • Payment rail adapters (Card, UPI, Agentic AP2)   │
│ (Standards-       │ • Synthetic financial transactions (24,000 samples)│
│  inspired models) │ • Adversarial Red Team attacks (9 vectors)         │
├───────────────────┼────────────────────────────────────────────────────┤
│ **NOT RUN**       │ • Public anchor dataset fidelity validation        │
│ (Missing licensed │   (PaySim and ULB datasets are proprietary and     │
│  external data)   │   not redistributed in this open repository)       │
└───────────────────┴────────────────────────────────────────────────────┘
```

---

## 4. Honest Prototype Limitations

When defending FORSETI, openly state these technical limitations:

1. **In-Memory Volatility:** All ledger state and authority records reside in Python application memory. Server restarts re-initialize state from default profiles (`auth_household_grocery_2026`). In production, this requires an ACID-compliant distributed transaction store.
2. **Development Key Management:** ML-DSA-44 private keys are held in memory and seeded for test reproducibility (`SEED=42`), rather than residing in a FIPS 140-3 Hardware Security Module (HSM).
3. **Synthetic Fraud Distribution:** The detection model is trained and benchmarked against synthetic adversary behavior. While statistical overlaps were engineered to avoid circularity, model performance on live bank fraud streams would require real banking data.

---

## Check yourself

1. **What is the actual number of collected pytest tests versus the number claimed in `README.md`?**
2. **Why is `dtl/feature_factory.py` considered vestigial?**
3. **What is the status of the public anchor fidelity test in `fidelity_report.json`?**
4. **Does the UI render the default placeholder signature from `models/proofs.py` as verified PQC status?**
5. **How many executable attack vectors are in `taxonomy.py` versus the claim in older markdown docs?**

<details>
<summary>Answers</summary>

<!--claims-ok--> (records the count as it stood when written)
1. Exactly 217 tests collected in code as of the tokenization/settlement follow-up (67 at the time this question was first written, when older markdown docs still claimed 52) — see LEARN_12 for the current per-file breakdown.
2. Because it implements an obsolete 13-feature extractor that is imported by no module; `detector/feature_schema.py` is the active 37-feature extractor across 6 groups.
3. `NOT RUN / DATASET UNAVAILABLE` because licensed PaySim and ULB CSV files are not redistributed.
4. No. The UI displays live PQC status dynamically fetched from the `/api/pqc/status` and `/api/pqc/verify` endpoints.
5. Exactly 17 executable vectors (and 46 research-only vectors), whereas older docs claimed 6, then 9, executable vectors.
</details>

---

## 5. Gaps Introduced By the Agentic Security Runtime Expansion (LEARN_16–21)

The modules covered in LEARN_16 through LEARN_21 introduced their own honestly-declared gaps, rather than silently working around them:

```
┌────────────────────────────────────────────────────────────────────────┐
│         AGENTIC SECURITY EXPANSION — DECLARED SCOPE BOUNDARIES         │
├────────────────────────────────────────────────────────────────────────┤
│ • Kill Chain: RESOLVED. All 11 stages now have an implemented vector - │
│   SETTLEMENT_CONFLICT and RECONCILIATION_DRIFT (LEARN_21) closed the   │
│   last two via a third parallel mechanism, app/settlement/.             │
│ • Settlement Reconciliation is deliberately narrow: it demonstrates    │
│   the CONCEPT of cross-system settlement inconsistency with two        │
│   synthetic 2-leg obligations per vector, not a general N-leg          │
│   reconciliation engine or real clearing/settlement infrastructure.     │
│ • Tokenization: the token store (`tokenization/store.py`) is in-memory │
│   and process-lifetime, same posture as the DTL ledger itself - it is  │
│   a synthetic scoped-token model, explicitly not a real network token  │
│   vault (Mastercard MDES etc.). See LEARN_21 §5.                        │
│ • Graph Sentinel: the entity graph is training-time only, built once   │
│   across the synthetic dataset generation run - there is no live,      │
│   per-round graph. A force-directed graph canvas in the UI was          │
│   explicitly descoped for exactly this reason (see LEARN_10 §6).        │
│ • Graph Sentinel's node schema is agent + merchant + device only, not  │
│   the full 11-node-type schema (User/SubAgent/Wallet/PaymentMethod/…)  │
│   some design documents describe - those entities have no backing      │
│   fields anywhere in the transaction model.                             │
│ • Unified Risk Engine: equal-weighted mean, explicitly NOT a fitted     │
│   model - there is no labelled severity dataset to fit weights          │
│   against, and presenting one would overstate the rigor behind it.      │
└────────────────────────────────────────────────────────────────────────┘
```

### The best case study in the whole codebase for "trust the numbers, not the assumption"

Building Graph Sentinel (LEARN_19) produced a genuine methodology bug: the first retrain scored a suspicious PR-AUC of exactly 1.0. Rather than accept it, the number itself was treated as a bug report, traced to merchant-identity leakage in the pre-existing dataset generator, fixed, and re-measured to a credible 0.9209. Full account in [LEARN_19 §3](LEARN_19_GRAPH_SENTINEL.md#3-the-case-study-a-suspicious-number-is-a-bug-report).

---

## Course Conclusion (Original 16-Chapter Core)

You have completed the original 16-chapter **FORSETI Codebase Tutor Course** covering the DTL core, simulator, ML detector, arena, crypto audit, AI agents, frontend, pipelines, and tests. The course continues with five more chapters covering the Agentic Payment Security Runtime expansion.

→ Continue to [LEARN_16 — The Agent Intent Firewall](LEARN_16_INTENT_FIREWALL.md)
