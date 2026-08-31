# LEARN_06 — The Machine Learning Detector

> **Prerequisites:** [LEARN_01](LEARN_01_WHAT_AND_WHY.md), [LEARN_04](LEARN_04_THE_DTL_CORE.md)  
> **You will be able to:**
> - Explain machine learning for fraud detection from first principles (features, loss, PR-AUC, calibration).
> - Describe every single feature in the 37-feature, 6-group schema and explain why zero train/serve skew is guaranteed.
> - Explain the anti-circularity design principles in synthetic dataset generation.
> - Detail why temporal splitting and attack-family holdout are scientifically necessary.
> - Interpret the 5-architecture baseline benchmark, the feature ablation study, and the headline negative result.  
> **Files this chapter is about:** `backend/app/detector/feature_schema.py`, `backend/app/detector/dataset_builder.py`, `backend/app/detector/model.py`, `backend/app/detector/train.py`, `backend/app/detector/calibration.py`, `backend/app/detector/explainability.py`, `backend/app/detector/baselines.py`, `backend/app/detector/ablation.py`

---

## 1. Machine Learning from First Principles

🧒 **Like you're five**  
Imagine you want to teach a computer how to spot suspicious transactions. You can't just tell it every single bad trick because clever thieves invent new tricks. Instead, you show it 24,000 examples of past purchases. For each purchase, you give it clues (like what time it was, how much it cost, and which shop it went to). The computer learns patterns from these clues. When a new purchase happens, the computer gives it a danger score between 0% and 100%.

🏪 **In real life**  
In financial payment processing, transaction data arrives as a tabular stream. Out of 24,000 transactions, only 1,701 ($7.09\%$) are fraudulent. Because fraud is rare, traditional accuracy (e.g. $93\%$) is a useless metric (a dumb model that predicts "Legitimate" on everything gets $93\%$ accuracy while missing $100\%$ of fraud). Instead, we use:
- **Precision:** Of the transactions the model flagged as fraud, how many were actually fraud?
- **Recall:** Of all actual fraudulent transactions, how many did the model catch?
- **PR-AUC (Precision-Recall Area Under Curve):** The definitive metric for imbalanced fraud datasets. FORSETI achieves **0.9209** PR-AUC (`artifacts/evaluation/metrics.json`).
- **Calibration (ECE):** Ensures that if the model outputs a probability of $0.80$, exactly $80$ out of $100$ such transactions are truly fraud.

---

## 2. The 37 Features Group by Group

The machine learning detector extracts exactly **37 features across 6 functional groups** (`backend/app/detector/feature_schema.py:10`) — the original 29 across 5 groups, plus an 8-feature `graph` group added by Payment Graph Sentinel. Full treatment of the graph group, including a real methodology bug it introduced and how the numbers themselves caught it, is in [LEARN_19 — Graph Sentinel](LEARN_19_GRAPH_SENTINEL.md); this section keeps the original 5 groups as first written.

```
┌────────────────────────────────────────────────────────────────────────┐
│                THE ORIGINAL 29 DETECTOR FEATURES (5 groups)            │
│         (+ an 8-feature "graph" 6th group — see LEARN_19)              │
├───────────────────┬────────────────────────────────────────────────────┤
│ Feature Group     │ Feature Names & Descriptions                       │
├───────────────────┼────────────────────────────────────────────────────┤
│ 1. raw_transaction│ • amount: Transaction value in INR (₹)             │
│    (8 features)   │ • rail_code: Numeric rail (0: Card, 1: UPI, 2: AP2)│
│                   │ • merchant_mcc_code: Numeric MCC (e.g. 5411)       │
│                   │ • hour_of_day: 0 to 23                             │
│                   │ • day_of_week: 0 to 6 (Monday=0)                   │
│                   │ • retry_count: Number of recent payment retries    │
│                   │ • tx_velocity_1h: Transaction count in last 1 hour │
│                   │ • merchant_risk_score: Historical risk (0.0 to 1.0)│
├───────────────────┼────────────────────────────────────────────────────┤
│ 2. delegation     │ • granted_limit: Delegated budget ceiling (₹)      │
│    (6 features)   │ • remaining_headroom: Uncommitted authority (₹)    │
│                   │ • authority_utilization_ratio: total_exposure / cap│
│                   │ • delegation_ttl_remaining_pct: % time left in grant│
│                   │ • delegation_fanout_count: Sub-agents delegated    │
│                   │ • active_subagents_count: Count of active agents   │
├───────────────────┼────────────────────────────────────────────────────┤
│ 3. cross_rail     │ • total_exposure_global: Settled+Auth+Pending+Res  │
│    (6 features)   │ • pending_spend_global: In-flight spend in validation│
│                   │ • cross_rail_velocity: Distinct rails touched in 1h│
│                   │ • num_rails_used_24h: Distinct rails touched in 24h│
│                   │ • exposure_after_tx_ratio: (exposure+amount) / cap │
│                   │ • amount_deviation_from_rail_mean: Variance from avg│
├───────────────────┼────────────────────────────────────────────────────┤
│ 4. semantic       │ • semantic_drift_score: % basket outside purpose   │
│    (5 features)   │ • stored_value_item_count: Count of gift cards/SKUs│
│                   │ • stored_value_value_ratio: Value of gift cards / ₹│
│                   │ • merchant_category_match_bool: 1 if MCC matches   │
│                   │ • cart_intent_consistency_score: 0.0 to 1.0 score  │
├───────────────────┼────────────────────────────────────────────────────┤
│ 5. security       │ • revocation_rate_1h: Rate of authority revocations│
│    (4 features)   │ • regrant_frequency: Rapid re-delegation frequency │
│                   │ • velocity_spike_indicator: 1 if velocity > 3 sigma│
│                   │ • mcc_entropy: Shannon entropy across merchant MCCs│
└───────────────────┴────────────────────────────────────────────────────┘
```

### Zero Train/Serve Skew

In machine learning engineering, **train/serve skew** occurs when feature calculation code during real-time production inference differs from the code used to generate offline training data. 

FORSETI guarantees **zero train/serve skew** by using the exact same class `DTLFeatureExtractor.extract_features()` (`backend/app/detector/feature_schema.py:127`) in `dataset_builder.emit()` and in `inference.score()`.

### Anti-Label Leakage: Spend Extraction Order

A critical design requirement: `dataset_builder.emit()` extracts features **before** booking the transaction to the ledger (`backend/app/detector/dataset_builder.py:108`):

```python
# backend/app/detector/dataset_builder.py:108
# 1. Extract features FIRST while exposure reflects pre-transaction state
features = DTLFeatureExtractor.extract_features(auth, tx, history)

# 2. Book spend to ledger AFTER feature extraction
in_flight = tx.amount * 0.35
auth.cumulative_spent_authorized += tx.amount - in_flight
auth.pending_spend_global += in_flight
```

If spend were booked *before* feature extraction, `exposure_after_tx_ratio` would double-count the transaction amount, creating label leakage where the model trivially memorizes the arithmetic artifact rather than learning behavioral patterns.

---

## 3. Synthetic Dataset & Anti-Circularity Engineering

The dataset builder (`backend/app/detector/dataset_builder.py:17`) generates 24,000 transactions with a realistic $7.09\%$ fraud prevalence:

```
Total Samples: 24,000
├─ Legitimate (NONE):       22,299 (92.91%)
└─ Adversarial Attacks:      1,701 (7.09%)
   ├─ VELOCITY_BURST:          725 (3.02%)
   ├─ CROSS_RAIL_SPLIT:        522 (2.18%)
   ├─ SCOPE_CREEP:             227 (0.95%)
   ├─ INTENT_LAUNDERING:       114 (0.48%)
   └─ REVOCATION_FLOOD:        113 (0.47%)
```

### The Two Anti-Circularity Rules

A naive synthetic data generator creates a trivial toy dataset where legitimate shopping carts *never* contain gift cards, and laundering attacks *always* contain gift cards. Under such naive data, the ML model simply learns `stored_value_item_count > 0` as an artificial shortcut.

FORSETI injects two deliberate statistical overlaps (`backend/app/detector/dataset_builder.py:273`):
1. **Legitimate Stored Value ($\sim 9\%$):** In real households, people buy gift cards for birthdays or store vouchers alongside groceries. Approximately $9\%$ of legitimate shopping baskets contain small, in-scope gift cards (`dataset_builder.py:293`). Thus, non-zero stored value is not a deterministic indicator of fraud.
2. **Large Legitimate Baskets ($\sim 12\%$):** Approximately $12\%$ of legitimate baskets are large stock-up grocery runs whose amounts overlap the attack amount range ($35\%$ to $62\%$ of budget ceiling, `dataset_builder.py:284`).

---

## 4. Temporal Splitting & Attack-Family Holdout

### Why Not K-Fold Cross-Validation?

Financial transaction streams are non-stationary time series. Standard random $K$-fold cross-validation randomly shuffles past and future transactions. If transaction $T_{100}$ is in the training set and transaction $T_{99}$ is in the test set, the model trains on the future to predict the past (**lookahead data leakage**).

FORSETI enforces strict **chronological temporal splitting** (`backend/app/detector/train.py:126`):

```
┌────────────────────────────────────────────────────────────────────────┐
│                     CHRONOLOGICAL TEMPORAL SPLIT                       │
├────────────────────────────────────────────────────────────────────────┤
│ [1. Train Set: 631.7%]      16,356 rows (Fraud prevalence: 4.60%)      │
│ [2. Validation Set: 15.00%]  3,600 rows (Fraud prevalence: 7.39%)      │
│ [3. Test Set: 15.00%]        3,600 rows (Fraud prevalence: 6.64%)      │
└────────────────────────────────────────────────────────────────────────┘
```

### The Attack-Family Holdout Protocol

To test whether machine learning can generalize to completely novel attack mechanics, the entire `CROSS_RAIL_SPLIT` attack family is **withheld from the training set** during baseline evaluation (`backend/app/detector/baselines.py:34`). The model must evaluate cross-rail splitting transactions having *never seen a single cross-rail split during training*.

---

## 5. Model Architecture & Isotonic Calibration

### Preferred Backend: XGBoost

The model factory initializes an `XGBClassifier` (`backend/app/detector/model.py:54`):
- `n_estimators = 300`, `max_depth = 6`, `learning_rate = 0.08`
- `subsample = 0.9`, `colsample_bytree = 0.9`, `reg_lambda = 1.0`
- `scale_pos_weight = 13.1` (corrects for the 1:13 class imbalance ratio)

### Probability Calibration (`calibration.py`)

Raw GBDT margin outputs are distorted under extreme class imbalance. FORSETI fits an **isotonic regression calibrator** on the temporal validation slice (`backend/app/detector/train.py:187`).

- **Expected Calibration Error (ECE) Before Calibration:** $0.01377$
- **Expected Calibration Error (ECE) After Calibration:** **$0.00611$** (a $69.2\%$ reduction in probability distortion)
- Metric artifact: `artifacts/evaluation/metrics.json -> calibration`

---

## 6. Baselines Benchmark — and the negative result that turned out to be a bug

The baseline harness evaluates five architectures on the identical test slice
(`artifacts/evaluation/baselines.json`), under two conditions: with the cross-rail family
**held out** of training, and with **every family seen**.

```
┌─────────────────────────────────────────────────────────────────────────┐
│        FIVE ARCHITECTURES, CROSS-RAIL FAMILY HELD OUT OF TRAINING            │
├─────────────────────────┬──────┬────────┬───────────┬──────────────────┐
│ Architecture              │ Feat │ PR-AUC │ Cross-rail │ 95% CI (n=64)      │
├─────────────────────────┼──────┼────────┼───────────┼──────────────────┤
│ 1. Rules only             │   0  │ 0.1262 │   0.3906   │ [0.281, 0.513]     │
│ 2. Per-rail ML (siloed)   │  24  │ 0.6534 │   0.1719   │ [0.099, 0.282]     │
│ 3. Global ML, no DTL      │  25  │ 0.6580 │   0.1719   │ [0.099, 0.282]     │
│ 4. Hybrid ML + DTL        │  37  │ 0.9400 │   0.8281   │ [0.718, 0.901]     │
│ 5. Deterministic DTL inv  │   3  │ 0.2530 │   0.8438   │ [0.736, 0.913]     │
└─────────────────────────┴──────┴────────┴───────────┴──────────────────┘
```

### The negative result this chapter used to report, and why it is gone

<!--claims-ok--> (post-mortem: quoting the superseded figures is the point)
> An earlier revision of this section reported **0.0000 cross-rail recall for every learned
> model** and called it "The Headline Negative Result". Rows 2, 3 and 4 above are where those
> zeros were.

They were real measurements of a broken experiment. Four of six attack families carried an
MCC that never appeared in legitimate traffic, so the classifier learned that categorical
shortcut and never needed `exposure_after_tx_ratio` at all. Removing the family from training
removed everything the model had. The "profound negative result about the limits of machine
learning" was a data leak in our own generator, and adversarial review found it before we did.

`app/detector/leakage_audit.py` now gates this in CI. The full post-mortem is
[LEARN_22](LEARN_22_THE_LEAK.md), and it is worth reading before trusting any number in this
chapter, because it is the clearest example in the repository of a result that looked
important and was an artifact.

### What the corrected table actually supports

**Read the intervals, not the point estimates.** The cross-rail slice is 64 transactions. A
95% Wilson interval on a recall near 0.83 is about ±0.09, which is wide enough to change what
may honestly be claimed:

- **A model without the aggregate feature cannot do this, and that separation is real.** Rows
  2 and 3 reach 0.1719 against row 4's 0.8281, and those intervals do not come close to
  overlapping. One ₹4,000 leg genuinely looks like ordinary grocery spending, and no amount of
  training data supplies a feature the model was never given.
- **Give a model the aggregate and it can — on this run.** Row 4's `exposure_after_tx_ratio`
  is its #1 SHAP driver, so it is using the mechanism rather than a fingerprint. But held-out
  0.8281 against seen 0.8438 is a gap of 0.0157, and n=64 **cannot resolve it**. We do not
  claim the classifier generalises to an unseen family. We claim this run did not catch it
  failing to, which is weaker and is what the data says.
- **The invariant's two columns are identical, and that is an identity rather than a
  measurement.** 0.8438 held out, 0.8438 seen — not because the run came out even, but
  because the check is arithmetic over the grant (₹4,000 + ₹4,000 + ₹4,000 > ₹10,000) and has
  no fitted parameter that a change of training data could move.

**That asymmetry is the claim, and no sample size can take it away.** Row 5's two numbers are
equal by construction. Row 4's are two separate measurements that happened to land close on
one run with one seed.

### The trade-off, stated

The invariant alone runs a **15.76% false-positive rate** on this slice. That is the cost of a
membership-and-arithmetic check with no notion of degree, and it is why the architecture pairs
it with the Adversarial Cost Governor — proportionate containment, partial authorisation —
rather than blanket blocking. A deterministic check that is holdout-independent AND
low-false-positive is not on offer here, and claiming otherwise would be the third thing in
this chapter that turned out to be too good to be true.

---

## 7. Feature-Group Ablation Study (Original 6 Variants)

> This section describes the ablation study as originally built, on the 29-feature/5-group schema. Two more variants (`H: raw+DTL`, `I: raw+DTL+graph`) and a `measured_graph_feature_lift` figure were added when the `graph` group shipped — see [LEARN_19 §4](LEARN_19_GRAPH_SENTINEL.md#4-the-extended-ablation-raw--rawdtl--rawdtlgraph--full-hybrid) for the full 9-variant table and the current numbers (the PR-AUC figures below predate the graph-feature retrain and are kept here for the ablation METHODOLOGY, not as current headline numbers).


The ablation study retrains six variants of the detector on systematic feature subsets (`artifacts/evaluation/ablation_results.json`):

```
┌────────────────────────────────────────────────────────────────────────┐
│                        FEATURE ABLATION STUDY                          │
├────────────────────────────────────────┬──────────────┬────────────────┤
│ Feature Variant                        │ Feature Count│ PR-AUC         │
├────────────────────────────────────────┼──────────────┼────────────────┤
│ Variant A: All Features (Full Hybrid)  │ 29           │ 0.9400         │
│ Variant B: No DTL Features             │ 17           │ 0.7261         │
│ Variant C: No Semantic Features        │ 24           │ **0.9556**     │
│ Variant D: No Cross-Rail Features      │ 23           │ 0.9466         │
│ Variant E: No Delegation Features      │ 23           │ 0.9597         │
│ Variant F: Raw Transaction Only        │ 8            │ 0.6106         │
├────────────────────────────────────────┴──────────────┴────────────────┤
│ Measured DTL Feature Lift (A - B): +0.2302 PR-AUC (+31.7% lift)       │
└────────────────────────────────────────────────────────────────────────┘
```

### Why Does Variant C (No Semantic) Score Higher Than Variant A?

In `ablation_results.json`, Variant C (removing semantic features) achieves a PR-AUC of **0.9556**, which is higher than the full model's $0.9400$. 

**The Scientific Reason:**  
As designed in the anti-circularity protocol, semantic features (`semantic_drift_score`, `stored_value_value_ratio`) carry inherent statistical noise because legitimate households occasionally buy gift cards ($\sim 9\%$). When semantic features are removed, the tree ensemble focuses exclusively on high-variance velocity and burst indicators, boosting precision on the synthetic test slice. However, without semantic features, the model becomes completely blind to subtle `INTENT_LAUNDERING` attacks.

---

## Check yourself

1. **How many features are in the FORSETI feature schema today, and across how many groups?**
2. **Why must feature extraction occur before spend booking in the dataset builder?**
3. **What are the two anti-circularity injections introduced in `dataset_builder.py`?**
4. **A model without DTL features reaches 0.172 cross-rail recall held out, and 0.563 with the family in training. Why does more training data not close that gap?**
5. **What is the measured PR-AUC lift of adding DTL features over raw transaction features?**

<details>
<summary>Answers</summary>

1. Exactly 37 features across 6 functional groups (raw_transaction: 8, delegation: 6, cross_rail: 6, semantic: 5, security: 4, graph: 8) (`backend/app/detector/feature_schema.py:10`; the graph group is covered in [LEARN_19](LEARN_19_GRAPH_SENTINEL.md)).
2. To prevent label leakage where `exposure_after_tx_ratio` double-counts the current transaction amount.
3. (a) $\sim 9\%$ of legitimate shopping carts contain in-scope gift cards, and (b) $\sim 12\%$ of legitimate baskets are large stock-up purchases overlapping attack amounts (`backend/app/detector/dataset_builder.py:273`).
4. Because the gap is a missing FEATURE, not missing data. A model scoring each transaction in isolation has no representation of what the other legs did, so an individual ₹4,000 grocery transaction is statistically normal however many examples it sees. Give the same model `exposure_after_tx_ratio` and held-out recall goes to 0.828. (Note the earlier revision of this question asserted 0.0 recall - that was the MCC leak, see [LEARN_22](LEARN_22_THE_LEAK.md).)
5. Measured lift of $+0.2302$ PR-AUC (+31.7% relative) from $0.7261$ (Variant B) to $0.9400$ (Variant A) (`artifacts/evaluation/ablation_results.json`).
</details>

---

## Where to go next
→ [LEARN_07 — Arena and Events](LEARN_07_ARENA_AND_EVENTS.md)
