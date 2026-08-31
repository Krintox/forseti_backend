# FORSETI — Complete Walkthrough

**Every page. Every button. What happens in the backend when you press it.**

If you have never seen this project before, read section 1, then do section 3. That is the demo.

---

## 1. The idea, in plain language

You hire a robot assistant to buy groceries. You tell it:

> “You may spend **up to ₹10,000**, on **groceries**.”

That is a *delegation*. The robot now holds some of your spending authority.

Here is the problem. Your money can leave your account through several different **rails** —
different roads a payment can travel:

- the **card** rail (your debit/credit card)
- the **UPI** rail (instant bank-to-bank)
- the **agentic** rail (a machine-to-machine payment mandate)

Each road has its own guard. Each guard only watches *their own road*.

So a dishonest robot does this:

| Road | Amount | What the guard thinks |
|---|---|---|
| Card | ₹4,000 | “₹4,000? That's fine, my limit is ₹10,000.” ✅ |
| UPI | ₹4,000 | “₹4,000? That's fine, my limit is ₹10,000.” ✅ |
| Agentic | ₹4,000 | “₹4,000? That's fine, my limit is ₹10,000.” ✅ |

**Three guards each said yes. You just lost ₹12,000 on a ₹10,000 budget.**

Nobody broke a rule *on their own road*. The rule that broke was the one **nobody was watching**:
your total.

FORSETI is the missing watcher. It stands above all three roads, adds up everything the robot
spends everywhere, and compares it to what you actually authorised.

```
Card ₹4,000  ─┐
UPI  ₹4,000  ─┼──►  FORSETI adds them up: ₹12,000
AP2  ₹4,000  ─┘                              vs
                              your limit:  ₹10,000
                                              ↓
                                    ₹2,000 OVER — STOP
```

**One sentence:** *ordinary systems check each transaction; FORSETI checks whether the agent is
still inside the authority you gave it, across everything at once.*

---

## 2. Starting the system

Two processes. Two terminals.

```bash
# Terminal 1 — the brain (API + engine)
cd backend
python -m uvicorn app.main:app --port 8000 --host 0.0.0.0

# Terminal 2 — the dashboard
cd frontend
npm run dev -- -p 3005
```

Open **http://localhost:3005**.

Bottom-left of the sidebar you should see a green pulsing dot and **“Live stream”**. That means the
dashboard is connected to the engine's live event feed. If it says “Reconnecting”, the backend
isn't up yet.

---

## 3. The 60-second demo

1. Click **Live Arena** in the sidebar.
2. Confirm **Delegated limit** reads `10000`.
3. Make sure the **Cross-Rail Split** attack tile is selected (it is by default, marked FLAGSHIP).
4. Press the red **EXECUTE ATTACK** button.
5. **Watch for about 14 seconds.** Do not click anything.

You will see, in order:

| Time | On screen | What the backend just did |
|---|---|---|
| ~0.5s | “PLANNING CROSS RAIL SPLIT: Rs 12,000 across 3 rail(s)” | Red agent selected its vector and target rails |
| ~1.3s | “STEP 1: SENDING Rs 4,000 VIA CARD” | Built transaction 1 |
| ~2.2s | “LOCALLY APPROVED — LOOKS NORMAL”, card tile turns blue, shows ₹4,000 | Card adapter checked *its own* limit and approved |
| ~2.7s | “AGGREGATING ALL RAILS: Rs 4,000 / Rs 10,000” | DTL added it to the global total |
| ~3.7s | “ML RISK 6.8%” | The trained XGBoost model scored it — **low, correctly** |
| ~5.4s | UPI tile lights up, ₹4,000 | Second rail approved |
| ~6.4s | “GLOBAL EXPOSURE Rs 8,000 / Rs 10,000” | Running total, meter turns amber |
| ~8.5s | Agentic tile lights up, ₹4,000 | Third rail approved |
| ~8.9s | “AGGREGATING ALL RAILS: **Rs 12,000 / Rs 10,000**” | The total now exceeds the grant |
| ~9.4s | 🔴 **“VIOLATION: INV_01_GLOBAL_BUDGET_EXCEEDED”** | The invariant fired |
| ~11.3s | “CONTAINING WITHOUT LOCKING THE USER OUT” | Cost governor chose partial authorisation |
| ~12.6s | “SIGNING DTL SNAPSHOT (ML-DSA-44)” | Post-quantum signature over the audit state |
| ~13.5s | “NEXT STRATEGY: INTENT LAUNDERING” | Red agent observed the block and picked a new plan |

**The point to notice:** all three rails said ✅. The card tile, UPI tile and agentic tile all read
“LOCALLY OK”. Only the box in the middle — FORSETI DTL — objected.

**Also notice the ML risk score is only ~6.8%.** That is not a bug, and we do not hide it. A single
₹4,000 grocery payment genuinely looks innocent. No classifier can catch this from one transaction.
Arithmetic on the total catches it. That is the entire argument of the project.

---

## 4. Every page, every control

### Sidebar (always visible)

- **15 navigation entries**, grouped Command / Operations / Science / Governance. Every one leads
  to a real page.
- **GLOBAL AUTHORITY panel** (bottom): live exposure vs ceiling with a colour bar —
  green under 80%, amber 80–99%, red at 100%+.
- **Live stream dot**: green = connected to the event feed.
- **MODEL OK / NO MODEL**: whether a trained model artifact is loaded.

---

### Page 1 — Overview `/`

The pitch page.

- **Thesis panel** — the one-line contrast between traditional controls and FORSETI.
- **Five stat cards** — global exposure, detector status, test PR-AUC, measured p99 latency, PQC
  status. Any card reading `NOT RUN` means that pipeline hasn't been executed; it is never filled
  with a placeholder.
- **Headline measured finding** — the cross-rail recall table. Read this one table if you read
  nothing else.
- **System health** — four live checks.
- **Where to look** — shortcut cards into the other pages.

*Backend:* `GET /api/health`, `/api/evaluation`, `/api/benchmark/latency`.

---

### Page 2 — Live Arena `/arena` ⭐ the judging page

**Left column — DELEGATED AUTHORITY**

| Control | What it does |
|---|---|
| **Delegated limit (₹)** text box | The ceiling you grant the agent. Type any number. |
| **APPLY** | `POST /api/arena/limit` — recomputes headroom instantly across every page |
| **₹5,000 / ₹10,000 / ₹12,000 / ₹20,000** | Shortcut buttons that fill the box |
| **Ceiling / Exposure / Headroom** | Live readout. Exposure and headroom animate as money moves |
| Progress bar | % of authority consumed |

> **Try this:** set the limit to **₹20,000**, press APPLY, then run the same attack. ₹12,000 now
> fits inside ₹20,000, so **no violation fires** and the Red team wins. Set it to **₹5,000** and the
> violation fires on the *second* rail instead of the third. The rule is arithmetic on your number —
> not a threshold we tuned.

**Left column — LAUNCH ATTACK**

| Control | What it does |
|---|---|
| Six attack tiles | Choose the vector. Cross-Rail Split is the flagship |
| **DTL defense** toggle | ON = FORSETI watches. **OFF = the legacy world** |
| **Speed** 0.5x / 1x / 2x | Pacing of the event stream |
| **EXECUTE ATTACK** | `POST /api/arena/round` — runs it for real |
| **RESET** | Clears exposure and rail counters back to a fresh grant |

> **Try this:** turn **DTL defense OFF** and attack. Every rail approves, exposure reaches ₹12,000
> over a ₹10,000 grant, nothing objects, and the round ends **RED TEAM WINS**. That is today's
> world. Toggle it back ON and run again. Same attack, contained. This A/B is the most persuasive
> 30 seconds in the demo.

**Middle — LIVE ATTACK FLOW**

The animated diagram. Boxes are components; the glowing arrow shows what is moving *right now*, and
the pill under the diagram spells it out in words. Rail tiles show the running amount each rail has
approved and a green “LOCALLY OK”. The DTL box shows `exposure / ceiling` live.

Below it, four explanation cards: why each rail said yes, what the DTL saw (with the formal
invariant expression and the arithmetic), the detection score, and the containment action.

**Right — LIVE BACKEND EVENT LOG**

Every backend event as a line, with a `+seconds` offset and the numbers behind it. Filter by
All / Attack / Rails / DTL / ML / Defense / Audit. **Follow** auto-scrolls.

*This log is the receipt.* Every animation you saw corresponds to a line here, and every line was
emitted by the engine — not by the browser.

---

### Page 3 — Attack Simulator `/simulator`

All **63 researched attack vectors**, each with a real-world citation.

- **Search / Channel / Surface / Severity / Agentic relevance** filters, and an
  **“Only executable vectors”** checkbox.
- Cards are marked **Executable** (17) or **Research only** (46). We never imply we run all 63.
- Executable cards have **“Execute in arena”**, which jumps to the arena and launches that vector.

*Backend:* `GET /api/attacks`, parsed from `docs/taxonomy.md`.

---

### Page 4 — Defense Center `/defense`

The seven invariants, each with its formal expression, what it catches, and how many times it has
fired this session:

- `INV_01_GLOBAL_BUDGET_EXCEEDED` — `settled + authorized + pending + reserved + new_tx <= ceiling`
- `INV_02_SEMANTIC_INTENT_DRIFT` — `cart.items.category NOT IN semantic_exclusions`
- `INV_03_UNAUTHORIZED_MCC` — `tx.merchant_mcc IN permitted_mccs`
- `INV_04_UNAUTHORIZED_RAIL` — `tx.rail IN permitted_rails`
- `INV_05_PER_TX_CAP_EXCEEDED` — `tx.amount <= per_transaction_cap`
- `INV_06_AUTHORITY_EXPIRED` — `now <= delegation_created_at + validity_window_hours`
- `INV_07_UNAUTHORIZED_BENEFICIARY` — `tx.vpa_delegate IN beneficiary_scope`

Plus the **graduated response ladder**: ALLOW → STEP_UP → PARTIAL_AUTH → QUARANTINE →
CAPABILITY_REDUCTION → REVIEW → BLOCK.

> **Why not just block?** Because blocking is itself an attack. Flood the system with revocations
> and you can force a lockout of the *legitimate* user — a denial of service. Partial authorisation
> clears the genuine ₹2,500 basket and isolates only the suspicious ₹1,500.

---

### Page 5 — Transaction Monitor `/transactions`

One row per transaction, with the two columns that matter side by side:

**Rail verdict** (`APPROVED`) vs **DTL verdict** (`CONTAINED`).

Rows showing APPROVED next to CONTAINED are the contradiction FORSETI exists to resolve.

---

### Page 6 — Delegation Ledger `/ledger`

The authority record, and the **two-phase exposure breakdown**: settled, authorized,
pending, reserved.

> **Why four buckets?** If you only count *settled* money, three transactions can each pass while
> all three are still in flight — a race. Counting authorized + pending + reserved against the same
> ceiling closes that window.

Also shows permitted MCCs, semantic exclusions, and a timeline of every exposure change.

---

### Page 7 — Agents `/agents`

The closed loop.

- **Red agent** — its next strategy and why it pivoted.
- **Blue system** — active policy and how it hardened.
- **Strategy scoring table** — every strategy with its score, prior, attempts observed,
  containment rate, mean detector score, feasibility and a written rationale.

> The Red agent is **not** an `if attack == X: try Y` lookup. Its score is derived from outcomes it
> actually observed: `prior × (1 − containment_rate) × (1 − mean_detection) × feasibility`. When
> cross-rail splitting gets contained, its score collapses and a different strategy wins the argmax.
> It is deterministic (no LLM) so the demo reproduces exactly.

---

### Page 8 — Threat Intelligence `/threat-intel`

Coverage of the researched surface by channel and by attack surface, plus the agentic-specific
vectors — the ones that only exist because an autonomous agent holds delegated authority.

---

### Page 9 — Detection Lab `/detection`

The science. Everything here is read from `artifacts/evaluation/`.

- **Five metric cards** — PR-AUC, ROC-AUC, F1, recall @ 0.5% FPR, net value saved.
- **Provenance** — architecture, backend version, dataset size, calibration ECE, and the exact
  date range of each split.
- **Attack-family holdout** — per-family scores for the withheld families.
- **Baselines** — both conditions (family held out, and family seen).
- **Ablation** — six retrained variants and the measured DTL lift.

Every panel shows its experiment ID and seed. Missing artifacts render as **NOT RUN** with the
command that generates them.

---

### Page 10 — Fidelity Lab `/fidelity`

Statistical realism checks against public datasets.

**This page currently reports `NOT RUN / DATASET UNAVAILABLE`, and that is correct.** PaySim and
the ULB credit-card dataset are licensed and are not redistributed with this repository. Run
`python scripts/download_anchors.py` for sources, drop the CSVs into `data/anchors/`, and re-run
the harness — KS, correlation distance, discriminator AUC and TSTR will populate.

The self-consistency figures shown compare synthetic against synthetic. They prove the pipeline
executes; they are **not** evidence of realism, and the page says so.

---

### Page 11 — Explainability `/explainability`

Real `shap.TreeExplainer` output.

- **Global importance** — mean |SHAP| per feature across the test slice.
- **Latest live transaction** — per-feature contributions, red pushing toward fraud, green toward
  legitimate.

If genuine SHAP were unavailable, the badge would read `model_feature_contribution` and the
fallback would never be called SHAP.

> SHAP is kept out of the inline scoring path deliberately, which is why the latency benchmark
> excludes it — a real authorizer computes explanations out-of-band.

---

### Page 12 — Policy Center `/policy`

The seven delegation policies, which one is active, the authority parameters, and every Blue
adaptation triggered by a real violation.

---

### Page 13 — Quantum Audit `/audit`

Post-quantum tamper-evidence.

- **Provider** — algorithm, backend, and the FIPS 204 key/signature sizes.
- **Signed snapshot** — the exact canonical JSON that was signed, its SHA-256, and the signature.
- **Tamper-detection proof** — four cases run live: untouched (must verify), amount mutated (must
  fail), signature byte flipped (must fail), wrong key (must fail).
- **Verify it yourself** — two buttons that call `POST /api/pqc/verify`. The second adds ₹5,000 to
  the payload and shows verification failing.

> **Why this matters:** an attacker who can silently edit the audit log can erase evidence of the
> theft. Signing the state makes tampering detectable. It is *not* what catches the fraud — the DTL
> does that. PQC is the integrity layer underneath, and it is deliberately the least important claim
> in the project.

---

### Page 14 — Replay & Demo `/replay`

Every round is recorded to `artifacts/events/*.jsonl` with per-event timing offsets.

- **Run flagship demo** — deterministic seed-42 round.
- **Play / Pause / Step / Restart**, speed 0.5x / 1x / 2x.
- **Recorded rounds** list — pick any past round and replay it.
- Each step shows the raw event JSON.

Replay reconstructs from the log using original timings, so you are reviewing exactly what
happened, not a re-simulation.

---

### Page 15 — System Settings `/settings`

- **Runtime** — API base, stream status, model backend, SHAP status, PQC backend.
- **Environment captured at training time** — seed, Python version, every package version.
- **Experiment artifacts** — present or NOT RUN, each with its regeneration command.
- **Measured latency budget** — the real p50/p95/p99 per stage.
- **Data safety** — the six non-negotiable constraints.

---

## 5. How a single transaction flows through the backend

```
  Red agent builds a transaction
            ↓
  [1] Rail adapter          card_adapter.py / upi_adapter.py / agentic_adapter.py
      Checks ONLY its own limit + MCC scope.        → APPROVED
            ↓
  [2] DTL ledger            dtl/ledger.py
      Adds it to global exposure across all rails.
            ↓
  [3] Invariant engine      dtl/invariant_engine.py
      settled + authorized + pending + reserved + new_tx <= ceiling ?
      If not → emits a machine-checkable proof object.
            ↓
  [4] Feature extractor     detector/feature_schema.py
      29 features. The SAME code runs in training and here — zero train/serve skew.
            ↓
  [5] ML detector           detector/inference.py
      Loads the trained artifact from disk. Never trains at startup.
            ↓
  [6] Cost governor         dtl/cost_governor.py
      Splits legitimate from suspicious value; picks the mildest sufficient action.
            ↓
  [7] PQC auditor           crypto/mldsa_audit.py
      Canonicalises the DTL state and signs it with ML-DSA-44.
            ↓
  [8] Feedback engine       feedback/adaptive_planner.py
      Red rescores its strategies; Blue hardens policy.
```

Each numbered step emits structured events, which are simultaneously (a) logged, (b) written to
JSONL for replay, and (c) broadcast over the WebSocket. **That is why the frontend cannot drift
from the backend** — it has no independent state of its own.

---

## 6. Who wins, and how you can tell

**RED wins when** the aggregate spend exceeds the delegated ceiling and nothing stops it. You will
see: rails green, exposure bar past 100%, no violation event, and `ATTACK SUCCEEDED — NO GLOBAL
CHECK`. Reproduce it by turning **DTL defense OFF**.

**BLUE wins when** the invariant fires before the objective completes. You will see:
`INVARIANT_VIOLATION`, a containment action, a signed audit snapshot, and
`ROUND COMPLETE — BLUE WINS`.

The winner is decided by the engine (`detected = proof is not None`) and reported in the
`ATTACK_COMPLETE` event — the UI only renders it.

---

## 7. Things a judge is likely to ask

**“Is the animation real or scripted?”**
Real. Open the event log beside the diagram — every animation frame maps to a logged backend
event with a timestamp. Turn the backend off and the arena goes idle instead of animating.

**“Your ML score on the flagship attack is low. Isn't that a failure?”**
Not on its own, and the honest version of this answer is more interesting than the one we used
to give. We withheld cross-rail splitting from training and measured **0.172** recall from a
model with no aggregate view, **0.828** from the same model given DTL aggregate features, and
**0.844** from the deterministic invariant, which scores the same with the family in training
or out of it.

So a single leg really is close to indistinguishable from ordinary spending *if nobody holds
the total* - that part is the finding. What is NOT a finding is any claim that learned models
cannot do this at all: give one the aggregate feature and it does fine. The argument for the
DTL is that the invariant's two columns are equal **by construction**, while the classifier's
are two measurements that happened to land close on one run of 64 cross-rail transactions -
close enough that the 95% intervals overlap and we do not claim it generalises.

<!--claims-ok--> (records what an earlier revision said)
An earlier revision of this answer said "0.0 recall from *every* learned model". That was a
leak in our own generator, not a property of ML - see `docs/LEARN_22_THE_LEAK.md`.

**“Is the DTL just a spending limit?”**
It is a spending limit *that no individual rail can see*, enforced on aggregate exposure including
in-flight authorisations, and paired with semantic-intent and scope invariants. The novelty is the
placement and the two-phase accounting, not the arithmetic.

**“Is the cryptography real?”**
Yes — FIPS 204 ML-DSA-44 with correct 1312/2560/2420 byte sizes. Press the tamper buttons on the
Quantum Audit page and watch verification fail on a mutated payload. If the library were missing
the page would read PQC MODULE UNAVAILABLE.

**“Is the SHAP real?”**
Yes — `shap.TreeExplainer`. The fallback path exists but is labelled
`model_feature_contribution` and is never called SHAP.

**“How realistic is your synthetic data?”**
Unvalidated, and we say so. The fidelity harness is real but has no anchor data because PaySim and
ULB are licensed. Fidelity Lab reads NOT RUN. That is the honest state.

**“What's fake?”**
Nothing is presented as measured that wasn't. What is *simulated* is clearly labelled: the rails
are standards-inspired synthetic adapters, the attacks are our model of adversary behaviour, and
46 of the 55 taxonomy vectors are research-only.

---

## 8. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Sidebar says “Reconnecting” | Backend not running | Start uvicorn on :8000 |
| Nothing animates on attack | Event stream not connected | Check the green dot; reload |
| ML score says NOT TRAINED | No model artifact | `python tasks.py train` |
| Metrics pages say NOT RUN | Pipelines not executed | `python tasks.py all` |
| Fidelity says DATASET UNAVAILABLE | Licensed anchors absent | `python scripts/download_anchors.py` |
| PQC says MODULE UNAVAILABLE | No FIPS 204 library | `pip install dilithium-py` |
| Rails start declining locally | Rail cycle counters filled | Press RESET |
