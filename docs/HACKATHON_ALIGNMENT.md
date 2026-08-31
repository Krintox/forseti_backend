# Validation against the GFF 2026 brief

Checked after implementation, against `CHIMERA.md` and the published GFF 2026 theme
(*“Potential to Impact: Trusted, Connected, Global Systems for Inclusive Finance”*. Pillars
**Agentic AI**, **Tokenisation**, **Quantum**).

Verdict: **the core thesis is intact and now actually executable.** The gaps below are real and
are listed so they can be argued honestly rather than discovered by a judge.

---

## 1. The hidden assumption: still the right target

> *That verifying the cryptography, ceilings, TTL and allowlists of a delegation mandate is the
> same as verifying that the authority being exercised is the authority the human actually
> granted.*

This survives scrutiny, and the implementation now **measures** it rather than asserting it. Under
attack-family holdout, models with no cross-rail view score **0.172 recall** on cross-rail
splitting, and only ~0.5 even when the family IS in training. Give a model the DTL's aggregate
features and it reaches **0.828**; the deterministic invariant reaches **0.844** having seen no
examples at all. The gap between 0.172 and 0.828 is the assumption, quantified: it is not about
model quality, it is about whether anything holds the aggregate.

Still true that no prior GFF/RBI/SEBI winner attacked delegation integrity itself: prior winners
built fraud detectors, mule-account tools, tokenised KYC and offline CBDC.

---

## 2. Pillar coverage

| Pillar | Status | Evidence |
|---|---|---|
| **Agentic AI** | **Strong** | Delegated authority is the core object. AP2-style agentic rail, sub-agent scope creep, adaptive Red agent with outcome-derived strategy scoring, closed Red/Blue loop. |
| **Quantum** | **Strong** | Genuine NIST FIPS 204 ML-DSA-44 (1312/2560/2420 byte sizes). Four tamper cases pass live. The signature now commits to the real event-log hash-chain root, not a placeholder. |
| **Tokenisation** | **Partial, the weakest pillar** | The card rail is a *tokenised* card adapter and stored-value/gift instruments are the semantic-drift target. But there is **no tokenised-deposit or stablecoin settlement rail**, which the brief explicitly called for. |

**Recommendation:** if there is time before judging, the single highest-value addition is a fourth
rail, a tokenised-deposit / stablecoin settlement leg. It is the cheapest way to move Tokenisation
from “implied” to “demonstrated”, and the DTL already generalises across rails, so the invariant
needs no change. Do **not** claim tokenisation depth until that exists.

---

## 3. Defense components from the brief

| Brief component | Status |
|---|---|
| Delegation-Trust Ledger (append-only, cross-rail) | **Implemented.** Now SHA-256 hash-chained, tamper-evident, with `/api/arena/verify-log` recomputing the chain and pinpointing the broken index. |
| Intent Invariant Engine + signed semantic-drift proof | **Implemented.** `INV_02_SEMANTIC_INTENT_DRIFT` emits a machine-checkable proof object. |
| Cross-rail state-consistency | **Implemented.** Two-phase exposure (settled + authorized + pending + reserved) closes the in-flight race. Post-authorization settlement/reconciliation divergence is now modelled deterministically too, see `app/settlement/reconciliation.py` (Settlement Conflict, Reconciliation Drift) and `docs/LEARN_18_KILL_CHAIN.md`. |
| Adversarial-Cost Governor / containment without blocking | **Implemented.** Seven-level ladder; partial authorisation splits legitimate from suspicious value. Never mass-revokes. |
| PQC quantum-agility layer | **Partial.** ML-DSA-44 signing is real. **ML-KEM (FIPS 203) key establishment, hybrid mode, and crypto-provenance down-scoping of deprecated-suite mandates are NOT implemented.** |
| Counterfactual provenance / canary exploration (Trained Blindness) | **Not implemented.** Named in the brief as a fourth defense; absent here. |
| Multi-round failure-of-failure arc | **Partial.** 17 attack vectors are executable and Red adapts after containment (see the Adaptive Immune System, `docs/LEARN_20_ADAPTIVE_IMMUNE_SYSTEM.md`), but a scripted escalating narrative (Blue fails at round N, learns by round N+1) is not staged as a fixed script. |

---

## 4. Evaluation-rubric fit

| Dimension | Evidence |
|---|---|
| Diversity of attacks | 63 researched vectors with citations; 17 deeply implemented and executable, spanning the seven authority dimensions, agent-reasoning integrity, and post-authorization settlement/reconciliation. The split is explicit everywhere, never implied that all 63 run. |
| Statistical realism | **Weakest dimension. Reports NOT RUN.** The harness (KS, JS divergence, correlation distance, discriminator, TSTR) is real and executes, but PaySim/ULB are licensed and absent, so no realism claim is made. |
| Detection efficacy | Real XGBoost, temporal split, attack-family holdout, five-architecture comparison, nine-variant ablation (incl. Payment Graph Sentinel), genuine SHAP. All from artifacts with experiment ID and seed. |
| Cryptographic provenance | Real ML-DSA-44 over a canonicalised state that commits to the hash-chained log. |
| Real-world feasibility | Measured p99 **0.879 ms** over 10,000 transactions, against a self-declared 30 ms budget. Graceful containment rather than blanket blocking. |

---

## 5. Honest risks going into judging

1. **Fidelity is unvalidated.** If a judge weights statistical realism heavily, this scores poorly
   and we should say so first rather than be caught. Mitigation: obtain the anchors and re-run,
   the harness is ready and takes seconds.
2. **Tokenisation is thin** for a three-pillar theme. See the recommendation above.
3. **DTL feature lift for the classifier is +0.2302 PR-AUC** (0.7261 -> 0.9563, same test slice,
   +31.7% relative). It holds at the deployed 0.5 threshold too (recall 0.886 vs 0.532), which an
   earlier revision's lift did not.
4. **The invariant alone has 15.8% FPR.** Lead with why the cost governor exists, before a judge
   raises it.
5. **Small holdout sample** (n=37 cross-rail attack rows in the metrics slice). Directionally
   consistent, but say "directionally" not "conclusively".
6. **Our own attack model.** Detection is measured against our simulation of adversary behaviour,
   not production fraud. This is inherent to a synthetic arena and should be stated up front.

---

## 6. The claim to make on stage

> Traditional payment controls ask whether a transaction is valid on its rail. Every rail in our
> demo answers yes, correctly, and the user still loses ₹2,000 beyond what they authorised.
>
> We measured what it takes to catch that. A model that sees one transaction at a time scores
> **0.172 recall** on cross-rail splitting held out, and still only **0.5** when we train it on
> the attack directly. The information isn't in the transaction. Give that same model the
> aggregate-authority features and it jumps to **0.828** on a family it has never seen. Our
> deterministic invariant gets **0.844** having seen nothing at all, and cannot be degraded by a
> novel attack family, because it is arithmetic over the grant rather than a learned threshold.
>
> That is the finding: this class of agentic-payment abuse is an **authority-accounting** problem
> before it is a detection problem. The detector only works once something computes the aggregate
> for it, and today nothing does. FORSETI is that accounting layer, it contains without shutting
> the user down, and its audit trail is signed with real FIPS 204 ML-DSA-44.
>
> We also publish how we got this wrong once: an earlier revision of these numbers was inflated by
> a categorical leak in our own data generator. We found it, fixed it, and now gate it in CI.

Every number in that claim is in `artifacts/`, reproducible with `python tasks.py all`, seed 42.
