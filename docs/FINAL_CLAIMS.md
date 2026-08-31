# FINAL_CLAIMS

Claim-by-claim ledger: what FORSETI is safe to say, where the number comes from, and the exact
wording to use. Current artifacts win over prior documentation, see
`docs/FINAL_IMPLEMENTATION_AUDIT.md` for the full module-by-module audit this ledger summarizes.

---

### Authority dimensions

**CLAIM:** "FORSETI protects delegated authority across seven dimensions."
**SOURCE:** `backend/app/models/state.py:30-44` (`AuthorityDimension` enum), `invariant_engine.py` (7 `INV_` checks).
**STATUS:** MEASURED / IMPLEMENTED. Deterministic, not a statistical claim.
**SAFE WORDING:** "FORSETI protects multidimensional delegated authority, amount, per-transaction size, rail, merchant, beneficiary, purpose, and time, not a single spend ceiling."

### Attack taxonomy

**CLAIM:** "63 researched attack vectors, 17 deeply implemented and executable."
**SOURCE:** `docs/taxonomy.md` (63 rows), `backend/app/taxonomy.py::IMPLEMENTED` (17 entries), confirmed via `TAXONOMY` parse (`len(TAXONOMY) == 63`, `implemented_count == 17`, both pinned by `tests/test_forseti.py::TestTaxonomy`).
**STATUS:** IMPLEMENTED.
**SAFE WORDING:** "17 executable vectors spanning the seven authority dimensions, agent-reasoning integrity and post-authorization settlement, all 17 in thesis scope. The catalogue holds 63 researched rows for landscape completeness; **41 are about delegated agent authority and 22 are not**, and the API says which is which (`in_thesis_scope`)."

**LEAD WITH 17, NOT 63.** A judge who samples three of the 46 research rows at random will find off-topic ones (smart-contract re-entrancy, payroll redirection, Android 2FA theft). They are real, cited threats that are not this project's subject. Severity and agentic-relevance on research rows are **keyword-inferred from our own description text**, exposed as `label_provenance`; never present them as researched ratings.

### ML detection: headline metrics

**CLAIM:** "XGBoost PR-AUC = 0.9209 (temporal test, attack-family holdout), ROC-AUC = 0.9766, 37 features across 6 groups."
**SOURCE:** `artifacts/evaluation/metrics.json` (`experiment_id: EXP-20260821-165229`).
**STATUS:** MEASURED.
**SAFE WORDING:** "On the current synthetic evaluation split, with `CROSS_RAIL_SPLIT` and `REVOCATION_FLOOD` held out of training entirely, the classifier scores PR-AUC 0.9209 / ROC-AUC 0.9766."

### Cross-rail split: the negative result

**CLAIM:** "Models without a cross-rail view score 0.172 recall on held-out cross-rail splitting and only ~0.5 even when trained on it; a model given the DTL's aggregate features reaches 0.828; the deterministic invariant reaches 0.844 with zero training."
**SOURCE:** `artifacts/evaluation/baselines.json` (`headline_finding`, both conditions).
**STATUS:** MEASURED.
**SAFE WORDING:** Show BOTH columns. The finding is that the information lives in the aggregate, not that ML fundamentally fails, an earlier revision claimed the latter and its own artifact refuted it. See `docs/LEARN_22_THE_LEAK.md`.

### Ablation (post-graph-retrain)

**CLAIM:** "Measured DTL feature lift +0.2302 PR-AUC (31.7% relative); measured graph feature lift +0.0095 PR-AUC (1.0% relative)."
**SOURCE:** `artifacts/evaluation/ablation_results.json` (`measured_dtl_feature_lift`, `measured_graph_feature_lift`), `experiment_id: ABLATION-20260821-165330`, 9 variants A-I.
**STATUS:** MEASURED.
**SAFE WORDING:** Use the exact figures above; do not round further or restate as "roughly."

### Baseline comparison table (5-architecture)

**CLAIM:** "Rules-only, per-rail ML, global ML (no DTL), hybrid ML+DTL, and DTL-invariant-only architectures compared on an identical held-out test slice."
**SOURCE:** `artifacts/evaluation/baselines.json`, `experiment_id: BASELINE-SUITE-20260818-143541`.
**STATUS:** MEASURED on the current 37-feature schema, regenerated after the categorical-leakage fix.
**SAFE WORDING:** Quote directly from `docs/MEASURED_NUMBERS.md`, which is generated from the artifacts by `scripts/check_claims.py`.

### Latency

**CLAIM:** "p99 0.879 ms full inline pipeline (10,000 transactions), against a self-declared 30 ms budget."
**SOURCE:** `artifacts/benchmark/latency.json` (`sla_verdict: "PASS - measured p99 0.8791 ms < 30.0 ms budget"`).
**STATUS:** MEASURED.
**SAFE WORDING:** State the 30 ms figure explicitly as FORSETI's own assumption, not a published network SLA.

### Public anchor fidelity

**CLAIM:** none currently makeable as a positive result.
**SOURCE:** `artifacts/fidelity/fidelity_report.json`, `"overall_status": "NOT RUN / DATASET UNAVAILABLE"`, `anchor_datasets_loaded: []`.
**STATUS:** NOT RUN / DATASET UNAVAILABLE. Neither PaySim nor the ULB creditcard CSV is present in `data/anchors/`; both require accepting Kaggle's terms under a user account, which this session cannot do on the user's behalf.
**SAFE WORDING:** "FORSETI includes a reproducible fidelity harness for public anchor datasets (KS test, Jensen-Shannon divergence, correlation distance, discriminator AUC, TSTR)." **NEVER** "FORSETI was statistically validated against PaySim/ULB" unless `fidelity_report.json` shows `anchor_datasets_loaded` non-empty and real metrics, not the current self-consistency-only figures.

### Cryptographic audit

**CLAIM:** "Genuine NIST FIPS 204 ML-DSA-44 signatures, verified live."
**SOURCE:** `backend/app/crypto/pqc_provider.py`; live-confirmed this session via `/api/health` (`pqc_backend: dilithium-py`) and a live arena round showing `ML-DSA-44 VERIFIED` after `create_signed_snapshot` → `run_tamper_test` (4/4 cases pass).
**STATUS:** MEASURED / IMPLEMENTED, genuinely verified (not merely present as a UI label).
**SAFE WORDING:** "FORSETI demonstrates post-quantum ML-DSA-44 signatures for tamper-evident delegation/audit records." **NEVER** "FORSETI makes payments quantum-safe". This is an audit-signing layer over the event log, not a property of any payment rail.

**KEY PROVENANCE, state this proactively.** The signing key is generated randomly per process, so a copy of this repository does **not** hold the key of a running instance. An earlier revision derived it from a seed hardcoded in the source, which meant anyone with the repo could forge any snapshot. It is still not HSM-backed and the key lives in process memory, so the honest claim is **tamper-evident against accidental or downstream modification, not against an adversary with host access.** `provider_status()` returns `hsm_backed: false` and a `security_posture` string saying exactly this. Set `FORSETI_PQC_SEED` only when byte-reproducibility is deliberately wanted.

**WHAT THE SIGNATURE COVERS:** authority identity, the ceiling, the four-bucket exposure breakdown, active policy, and the event-log hash-chain head. Invariant proofs, transactions and containment decisions are covered **transitively** through that head, say "transitively", not "we sign everything".

### AI advisory layer

**CLAIM:** "12 advisory AI agents; none of them decide an authorization outcome."
**SOURCE:** `backend/app/ai/agents.py::SYSTEM_HIERARCHY` and `AGENT_CATALOG` (`len == 12`).
**STATUS:** IMPLEMENTED, correctly framed in code already.
**SAFE WORDING:** "FORSETI uses advisory AI agents for compilation, explanation, investigation, strategy suggestion and governance. Deterministic security controls remain authoritative." **NEVER** "12 AI agents make payment decisions" or "the LLM approved the payment."

### Payment rails

**CLAIM:** three rail adapters model card, UPI, and agentic payment flows.
**SOURCE:** `backend/app/simulator/adapters/{card,upi,agentic}_adapter.py`.
**STATUS:** SIMULATED.
**SAFE WORDING:** "Standards-inspired synthetic card-token rail," "UPI-Circle-inspired synthetic delegation rail," "AP2-style synthetic agentic rail." **NEVER** "local MDES implementation," "real UPI implementation," or "cloned AP2."

### Tokenisation

**CLAIM:** a synthetic scoped-token credential model demonstrating token-scope enforcement chained to live delegated authority.
**SOURCE:** `backend/app/tokenization/`; `tests/test_tokenization.py` (20/20 passing), including a direct test that a token cannot outlive a delegation narrowed after issuance.
**STATUS:** IMPLEMENTED, new this session.
**SAFE WORDING:** "A synthetic scoped-token model that demonstrates how tokenized payment credentials can inherit and enforce delegated authority, not an implementation of Mastercard MDES or any real network token vault." Token store is in-memory/process-lifetime, say so if asked about persistence.

### Settlement Conflict / Reconciliation Drift

**CLAIM:** two new attack vectors close the last two unmapped Kill Chain stages (10, 11), via a third parallel deterministic mechanism distinct from DTL invariants and Deception Lab.
**SOURCE:** `backend/app/settlement/reconciliation.py`; `tests/test_settlement_reconciliation.py` (19/19 passing, including proof that no authority-dimension invariant fires on either vector, the point of the demonstration).
**STATUS:** IMPLEMENTED, new this session.
**SAFE WORDING:** "FORSETI models two synthetic post-authorization lifecycle failures, cross-rail settlement conflict and same-rail reconciliation drift, as safe, bounded synthetic scenarios. This is not a model of real banking clearing/settlement exploitation."

**CONCEDE THE PRECEDENT FIRST.** Duplicate-settlement detection by shared identifier **is idempotency**, and Stripe/Adyen/Square all ship it. Do not present it as novel. What is not standard: the key is a business-level *obligation* rather than a client-supplied request id (a different key makes the same transaction look new, the documented weakness of idempotency keys), the check is **cross-rail** where no single processor sees both legs, and containment releases *delegated authority*, which an idempotency key has no concept of. Not modelled: partial captures, late presentment, representments, chargebacks, multi-currency, cut-off windows, ARN/RRN matching. Call it "reconciliation checks", not an engine.

### Unified Risk Engine

**CLAIM:** an explicit, auditable aggregation of independently-computed risk signals, with deterministic override.
**SOURCE:** `backend/app/risk_engine/risk.py`, `weighting: "equal-weighted mean - no labelled severity dataset exists to fit weights against"`.
**STATUS:** IMPLEMENTED, correctly framed in code already; no change made.
**SAFE WORDING:** "Unified risk aggregation combines independently interpretable risk components with deterministic authority overrides." **NEVER** "AI learned the optimal risk weighting". There is no fitted model here by design.

### Test suite

**CLAIM:** "455 automated backend tests and 116 browser checks, 100% passing."
**SOURCE:** `cd backend && python -m pytest tests/ -q` → `455 passed`, recorded to
`artifacts/tests/test_inventory.json` by `python scripts/check_claims.py --collect-tests`.
Browser: `cd frontend && npm run e2e` → 72 responsive checks (18 routes × 4 viewports)
and 44 functional checks, 0 console errors.
**STATUS:** MEASURED, verified live. The count is now **gated**: `check_claims.py`
fails if any document quotes a backend test count that is not the collected one,
added after four documents were found still claiming 217.
**SAFE WORDING:** State both numbers and say what the browser suite is *for*: it
covers what pytest structurally cannot see, and it found four real defects on its
first run, including a policy ladder in the UI that had drifted from the backend
enum and was missing its top rung.

### Production limitations (state explicitly when asked)

1. Ledger and token-store state are in-memory, process-lifetime, not a persistent/ACID store.
2. The PQC signing key is **randomly generated per process** and held in memory, no key ships in the repository, and none is HSM-protected. Status reports `hsm_backed: false` with explicit provenance. The property is tamper-EVIDENCE against accidental or downstream modification, not resistance to an adversary with host access.
3. ML performance is measured against synthetic adversarial behaviour, not live bank fraud streams.
4. Public anchor fidelity is unexecuted pending licensed dataset availability.
5. Graph Sentinel is a training-time construct; there is no live per-round cross-authority graph.
6. Tokenisation is a synthetic model inspired by real token-lifecycle concepts, not a real network token vault.
7. Payment rails are standards-inspired synthetic adapters, not connections to production networks.

Never disguise these as solved. Prefer: "synthetic," "standards-inspired," "prototype," "research runtime," "measured on synthetic data," "advisory AI," "training-time graph," "post-quantum audit prototype."
