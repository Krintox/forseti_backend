# LEARN_12: Automated Tests & Self-Verification

> **Prerequisites:** [LEARN_03](LEARN_03_MAP_OF_THE_CODEBASE.md), [LEARN_11](LEARN_11_PIPELINES_AND_ARTIFACTS.md)  
> **You will be able to:**
> - Run the complete 455-test backend suite and the 116-check browser suite, and interpret what each class of test is actually protecting.
> - Execute targeted test subsets for PQC, authority dimensions, and rail simulators.
> - Verify every headline metric and claim yourself using standalone Python command snippets.
> - Distinguish pytest collection from the standalone `verify_all.py` runbook, and know why the browser suite exists separately from both.  
> **Files this chapter is about:** `backend/tests/` (29 files), `backend/tests/verify_all.py`, `frontend/e2e/`

---

## 1. The 455-Test Backend Suite

🧒 **Like you're five**
Before an astronaut gets into a spaceship, the ground crew runs a checklist of safety lights: engine check, radio check, air tank check, door lock check. If all the lights turn green, the spaceship is ready to fly. In FORSETI, `python tasks.py test` checks 455 safety lights.

🏪 **In real life**
In regulated financial technology, a code change must prove that security invariants and data schemas survived it. This suite spans invariant boundary conditions, multi-rail simulation limits, feature-schema consistency between training and serving, PQC lattice verification, AI-layer fallback handling, concurrency under contention, and the tokenization and settlement modules.

🎓 **Properly**

```bash
cd backend && python -m pytest tests/ -q
# Output: 455 passed
```

```
┌────────────────────────────────────────────────────────────────────────┐
│                  455-TEST SUITE CLASSIFICATION MATRIX                  │
├────────────────────────────────┬───────┬───────────────────────────────┤
│ Test File                      │ Tests │ What it protects              │
├────────────────────────────────┼───────┼───────────────────────────────┤
│ `test_forseti.py`              │ 51    │ Core DTL, simulator, features,│
│                                │       │ model, PQC, arena, taxonomy   │
│ `test_containment_is_          │ 26    │ Containment outcome is        │
│ structured.py`                 │       │ machine-readable and matches  │
│                                │       │ what the ledger booked        │
│ `test_containment_is_          │ 19    │ A new violation can never     │
│ monotonic.py`                  │       │ RELAX containment             │
│ `test_cross_rail_double_       │ 9     │ One obligation captured on    │
│ settlement.py`                 │       │ two rails (RECON_03)          │
│ `test_suspension_is_enforced.py`│ 32   │ INV_08: a suspended mandate   │
│                                │       │ authorises nothing (LEARN_20) │
│ `test_authority_dimensions.py` │ 25    │ Non-monetary dimensions,      │
│                                │       │ counterfactual isolation      │
│ `test_adversarial_realism.py`  │ 22    │ Attacks do not announce       │
│                                │       │ themselves to the detector    │
│ `test_tokenization.py`         │ 20    │ Token lifecycle, dual scope   │
│                                │       │ enforcement (LEARN_21)        │
│ `test_authority_engine_        │ 20    │ Ceiling never mutated, proof  │
│ hardening.py`                  │       │ precedence, SKU attestation   │
│ `test_settlement_              │ 19    │ Settlement Conflict,          │
│ reconciliation.py`             │       │ Reconciliation Drift (LEARN_21)│
│ `test_graph_sentinel.py`       │ 17    │ Non-leakage, dataset          │
│                                │       │ integration (LEARN_19)        │
│ `test_deception_lab.py`        │ 17    │ 4 detectors + non-authority-  │
│                                │       │ outcome-change proof (LEARN_17)│
│ `test_client_signature.py`     │ 17    │ Rails verify a real HMAC over │
│                                │       │ canonical content, not a      │
│                                │       │ self-declared "valid" string  │
│ `test_policy_ladder.py`        │ 16    │ One source of truth for the   │
│                                │       │ escalation ladder (LEARN_20)  │
│ `test_adaptive_immune.py`      │ 16    │ Escalation ladder, campaign   │
│                                │       │ runner (LEARN_20)             │
│ `test_beneficiary_             │ 14    │ The spoofed biller lookup is  │
│ mechanism.py`                  │       │ a real directory, not narrated│
│ `test_statistical_honesty.py`  │ 13    │ Every published recall carries │
│                                │       │ its 95% interval; the claims  │
│                                │       │ match what n supports         │
│ `test_chain_score_             │ 14    │ attack_chain_score is two     │
│ independence.py`               │       │ magnitudes, not a disguised   │
│                                │       │ boolean                       │
│ `test_kill_chain.py`           │ 13    │ Stage mapping, per-round      │
│                                │       │ score, coverage (LEARN_18)    │
│ `test_intent_firewall.py`      │ 13    │ BENEFICIARY dim, INV_07,      │
│                                │       │ drift vector (LEARN_16)       │
│ `test_audit_and_scope_         │ 11    │ PQC key provenance, taxonomy  │
│ honesty.py`                    │       │ scope, advisory-AI boundary   │
│ `test_risk_engine.py`          │ 10    │ Five MUTUALLY INDEPENDENT     │
│                                │       │ risk components (LEARN_20)    │
│ `test_leakage_and_skew.py`     │ 10    │ Categorical leakage audit,    │
│                                │       │ train/serve skew (LEARN_22)   │
│ `test_counterfactual_          │ 8     │ RAIL/PURPOSE dimension-gating │
│ dimensions.py`                 │       │ (LEARN_09)                    │
│ `test_p1_intelligence.py`      │ 7     │ Incident report appendix,     │
│                                │       │ Agent Council roster (LEARN_09)│
│ `test_ledger_concurrency.py`   │ 6     │ Atomic reserve under 60       │
│                                │       │ threads, plus an UNSAFE       │
│                                │       │ control that must overspend   │
│ `test_run_state_never_         │ 4     │ Run-state flags unwind on the │
│ latches.py`                    │       │ failure path, not just the    │
│                                │       │ happy one                     │
│ `test_simulator.py`            │ 2     │ Local rail approvals & limits │
│ `test_dtl_defense.py`          │ 2     │ Partial auth, cross-rail split│
├────────────────────────────────┼───────┼───────────────────────────────┤
│ TOTAL AUTOMATED PYTESTS        │ 455   │ 100% passed                   │
└────────────────────────────────┴───────┴───────────────────────────────┘
```

### Two tests worth reading before the rest

Most of the suite asserts that correct code stays correct. Two files are built
differently, and they are the ones to read if you only read two.

**`test_ledger_concurrency.py` ships a control that is supposed to FAIL.**
Proving that `try_reserve()` is atomic requires showing that the *non-atomic*
version it replaced was not. So the file contains a deliberate check-then-act
implementation, runs 60 threads at it, and asserts it **overspends**. If that
control ever stops overspending, the test that matters has stopped meaning
anything, a race that no longer reproduces cannot demonstrate that the fix
prevents it.

**`test_suspension_is_enforced.py` ships a control that is supposed to PASS.**
It runs one innocuous ₹100 payment against every rung of the escalation ladder.
Under all seven lesser rungs it must be **allowed**; only under
`AGENT_SUSPENDED` may it be rejected. Without that half, the test would pass
equally well against an engine that rejected everything.

---

## 1b. The browser suite: `frontend/e2e/`

The backend suite cannot see the surface a judge touches. Two Node scripts do,
driving a real browser against a live stack:

```bash
cd frontend
npm run e2e:responsive   # 18 routes x 4 viewports = 72 checks
npm run e2e:functional   # 44 checks: content, live SSE, a real attack, a real campaign
npm run e2e              # both; non-zero exit on failure
```

They exist because they found four defects that 455 green backend tests could
not have:

| Suite | Defect |
|---|---|
| responsive | A `w-60 shrink-0` sidebar left about 150 px of usable content at 390 px, pushing three pages past the right edge |
| responsive | Explainability's SHAP rows reserved 304 px of fixed-width columns |
| functional | The Policy Center kept a hand-written copy of the escalation ladder that had drifted, `AGENT_SUSPENDED` was missing, so the top rung highlighted nothing |
| functional | A client disconnecting mid-run latched `is_running` server-side, disabling every control until the process restarted |

The functional suite's sharpest check is the cheapest one: it fails if the
literal strings `undefined`, `NaN` or `[object Object]` reach the DOM on any
route. A backend field renamed in a refactor still typechecks on the frontend
wherever it is read off an `any`, and then renders as the word "undefined" in
front of whoever is watching.

---

*Note on `backend/tests/verify_all.py`:* This file contains a standalone Python `unittest` runner holding high-level integration checks. It is designed to be run directly via `python backend/tests/verify_all.py` and is not collected by standard `pytest tests/`.

*Note on running bare `pytest`:* `backend/pytest.ini` scopes discovery to `tests/` (`testpaths = tests`). Without it, a bare `pytest` invoked from `backend/` would also try to collect `app/fidelity/categorical_test.py` and `app/fidelity/ks_test.py`, implementation modules that happen to match pytest's default `*_test.py` discovery pattern, and crash on their relative imports before running anything.

---

## 2. Running Targeted Test Suites

You can execute targeted subsets of the test suite from the repository root:

```bash
# 1. Run full test suite via task runner
python tasks.py test

# 2. Run ONLY Post-Quantum Cryptography tamper tests
python tasks.py pqc-test

# 3. Run ONLY Multidimensional Authority tests
cd backend && python -m pytest tests/test_authority_dimensions.py -v

# 4. Run ONLY Core Defense tests
cd backend && python -m pytest tests/test_dtl_defense.py -v
```

---

## 3. Self-Verification Protocol for Headline Claims

Never trust prose without verifying the artifacts. Run these one-liners in your shell to verify every headline claim:

### Claim 1: "The invariant is holdout-independent; the classifier is not."
```bash
python -c "import json; b=json.load(open('artifacts/evaluation/baselines.json'))['headline_finding']; o=b['cross_rail_split_recall_when_family_held_out']; s=b['cross_rail_split_recall_when_family_seen']; print('invariant  held-out/seen:', o['dtl_invariant_only'], '/', s['dtl_invariant_only']); print('hybrid ML  held-out/seen:', o['hybrid_dtl_ml'], '/', s['hybrid_dtl_ml']); print('no-DTL ML  held-out/seen:', o['ml_without_dtl'], '/', s['ml_without_dtl'])"
```
**Expected Output:**
```
invariant  held-out/seen: 0.8438 / 0.8438
hybrid ML  held-out/seen: 0.8281 / 0.8438
no-DTL ML  held-out/seen: 0.1719 / 0.5625
```

Read the three rows together, because the comparison is the finding:

- the invariant's two numbers are **the same number**. It is arithmetic over
  the grant, so withholding the family costs it nothing
- hybrid ML lands within 0.016 of its seen-family score, which is what
  generalisation looks like: given the aggregate feature, it learned the
  mechanism
- a model **without** a cross-rail view reaches 0.1719 held out and still only
  0.5625 with the family in training, one ₹4,000 leg genuinely does look like
  ordinary grocery spending, and more data does not supply a missing feature

> **An earlier revision of this chapter claimed `ML Holdout Recall: 0.0`.** That
> number was real, and it was an artifact of a leak in our own generator rather
> than a property of learned models. Four of six attack families carried an MCC
> that never appeared in legitimate traffic, so the classifier learned that
> shortcut and never needed the aggregate feature; removing the family removed
> everything it had. See [LEARN_22](LEARN_22_THE_LEAK.md) for the post-mortem.
> The `headline_finding.claim` string in the artifact is now **generated from
> the measured recalls** rather than hardcoded, so it cannot survive a change in
> the numbers it describes.

---

### Claim 2: "Inline end-to-end latency p99 is 0.8791 ms."
```bash
python -c "import json; l=json.load(open('artifacts/benchmark/latency.json')); print('Measured p99:', l['breakdown']['full_end_to_end_pipeline']['p99_ms'], 'ms'); print('SLA Verdict:', l['metadata']['sla_verdict'])"
```
**Expected Output:**
```
Measured p99: 0.8791 ms
SLA Verdict: PASS - measured p99 0.8791 ms < 30.0 ms budget
```

---

### Claim 3: "DTL feature groups deliver +0.2302 PR-AUC lift (+31.7%)."
```bash
python -c "import json; a=json.load(open('artifacts/evaluation/ablation_results.json')); print('All Features PR-AUC:', a['measured_dtl_feature_lift']['pr_auc_all_features']); print('No-DTL PR-AUC:', a['measured_dtl_feature_lift']['pr_auc_without_dtl']); print('Lift:', a['measured_dtl_feature_lift']['lift'])"
```
**Expected Output:**
```
All Features PR-AUC: 0.9400
No-DTL PR-AUC: 0.7261
Lift: 0.2302
```

---

### Claim 4: "NIST FIPS 204 ML-DSA-44 catches all 4 cryptographic tamper scenarios."
```bash
python tasks.py pqc-test
```
**Expected Output:**
```
test_sign_and_verify_roundtrip PASSED
test_modified_message_fails_verification PASSED
test_modified_signature_fails_verification PASSED
test_wrong_key_fails_verification PASSED
```

---

## Check yourself

1. **How many automated tests are collected by `pytest backend/tests/` today, and how many test files does that span?**
2. **What test class verifies that `DTLFeatureExtractor` emits identical features in training and serving?**
3. **What command verifies the 4 post-quantum tamper scenarios?**
4. **Why is `verify_all.py` not counted in the pytest suite?**
5. **Which two test files ship a deliberate control, and what does each control prove?**

<details>
<summary>Answers</summary>

1. 455 tests across 29 files (see the matrix above), plus 116 browser checks in `frontend/e2e/` that pytest does not collect.
2. `TestFeatures` in `backend/tests/test_forseti.py` (`test_extractor_emits_the_declared_schema`, line 235).
3. `python tasks.py pqc-test`.
4. Because it is written as a standalone `unittest` script with custom runner hooks, rather than a pytest module.
5. `test_ledger_concurrency.py` ships an UNSAFE check-then-act implementation and asserts it **overspends** under 60 threads, if that stops failing, the atomic test proves nothing. `test_suspension_is_enforced.py` ships a clean ₹100 payment and asserts every rung *except* `AGENT_SUSPENDED` **allows** it, otherwise an engine that rejected everything would pass.
</details>

---

## Where to go next
→ [LEARN_13. Glossary](LEARN_13_GLOSSARY.md)
