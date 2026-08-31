# Machine Learning & Detection Methodology

## 1. Feature Engineering (6 Formal Feature Groups)
FORSETI extracts a 37-dimensional feature vector across six structured groups using a unified, deterministic feature extractor (`DTLFeatureExtractor`, `app/detector/feature_schema.py`):
- **Group A (Raw Transaction Features, 8)**: `amount`, `rail_code`, `merchant_mcc_code`, `hour_of_day`, `day_of_week`, `retry_count`, `tx_velocity_1h`, `merchant_risk_score`.
- **Group B (Delegation Features, 6)**: `granted_limit`, `remaining_headroom`, `authority_utilization_ratio`, `delegation_ttl_remaining_pct`, `delegation_fanout_count`, `active_subagents_count`.
- **Group C (Cross-Rail Features, 6)**: `total_exposure_global`, `pending_spend_global`, `cross_rail_velocity`, `num_rails_used_24h`, `exposure_after_tx_ratio`, `amount_deviation_from_rail_mean`.
- **Group D (Semantic Features, 5)**: `semantic_drift_score`, `stored_value_item_count`, `stored_value_value_ratio`, `merchant_category_match_bool`, `cart_intent_consistency_score`.
- **Group E (Security & Anomaly Features, 4)**: `revocation_rate_1h`, `regrant_frequency`, `velocity_spike_indicator`, `mcc_entropy`.
- **Group F (Payment Graph Sentinel, 8)**: `graph_agent_out_degree`, `graph_merchant_in_degree`, `graph_agent_pagerank`, `graph_merchant_pagerank`, `graph_agent_betweenness`, `graph_community_size_ratio`, `graph_device_shared_count`, `graph_cross_rail_fanout_velocity` - the only group that sees a pattern across DIFFERENT authorities; every other group is computed from one authority's own state and history alone. See `docs/LEARN_19_GRAPH_SENTINEL.md`.

## 2. Non-Circular Dataset & Temporal Splitting
To prevent the model from learning synthetic generator artifacts:
- Features are strictly derived from raw transaction properties and DTL state invariants, never from internal attack tags.
- The dataset is partitioned chronologically into **70% Train**, **15% Validation**, and **15% Test** splits.

## 3. Attack-Family Holdout Evaluation
The model is trained on a subset of attack families and evaluated against completely held-out families (e.g. `CROSS_RAIL_SPLIT` and `REVOCATION_FLOOD` withheld from training) to measure true generalized anomaly detection capability.

## 4. Probabilistic Calibration & Efficacy Metrics
- Base model: XGBoost `XGBClassifier` (gradient-boosted decision trees) when available, with class rebalancing - LightGBM as a fallback, and scikit-learn `HistGradientBoostingClassifier` as the last-resort fallback when neither is installed (`app/detector/model.py`). `artifacts/evaluation/metrics.json` records which backend actually produced the current measured numbers.
- Output probabilities calibrated via isotonic regression (`ProbabilityCalibrator(model, method="isotonic")`, `app/detector/calibration.py`) fit on a held-out temporal validation slice, strictly disjoint from both train and test. Platt/sigmoid calibration is a supported alternative method on the same class, not what is actually run.
- Metrics evaluated: PR-AUC (primary), ROC-AUC, Precision, Recall, F1, Recall @ 0.5% FPR, Precision@100, and Net INR Saved.
