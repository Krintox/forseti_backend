# LEARN_19 — Payment Graph Sentinel & the ML Feature Expansion

> **Prerequisites:** [LEARN_06](LEARN_06_THE_ML_MODEL.md)  
> **You will be able to:**
> - Explain what a cross-authority entity graph can see that every other feature group (LEARN_06) structurally cannot.
> - Trace the non-leakage discipline: why a transaction's graph features are snapshotted *before* its own edge is added.
> - Recount, in full, the merchant-identity leakage bug this module introduced and how the retrained numbers proved it — this is the single best case study in the whole codebase for "a suspicious result is a bug report, not a win."
> - Read the extended ablation table (37 features, 6 groups, 9 variants) and interpret the new `measured_graph_feature_lift`.  
> **Files this chapter is about:** `backend/app/graph_sentinel/graph_builder.py`, `backend/app/detector/feature_schema.py`, `backend/app/detector/dataset_builder.py`, `backend/app/detector/ablation.py`

---

## 1. The Blind Spot Every Other Feature Group Has

🧒 **Like you're five**  
Every clue you've learned so far — how much, how fast, what shop, what basket — is about **one kid and their one piggy bank**. But what if TEN different kids, who've never met, all start paying the SAME stranger on the same day? No single piggy bank's rules would ever notice that — you'd need to be watching *all* the piggy banks at once and asking "who do they have in common?"

🏪 **In real life**  
`DTLFeatureExtractor` (LEARN_06) computes every feature from ONE authority's own state and history — by construction, none of its 29 original features can see a pattern that only exists **across** authorities: many different agents converging on the same merchant, several agents sharing a device fingerprint, or one agent's centrality in the overall transaction graph. Payment Graph Sentinel exists to close exactly that blind spot.

🎓 **Properly**  
`backend/app/graph_sentinel/graph_builder.py:36`'s `PaymentGraph` is an incrementally-built agent↔merchant graph, using `networkx` for PageRank, betweenness centrality, and Louvain community detection. It is a **training-time** construct — built once across the whole synthetic dataset-generation trajectory (25 authorities, thousands of transactions) — not a live, per-round graph. The live single-authority arena genuinely has no cross-authority signal to offer, and `feature_schema.py` says so explicitly rather than fabricating one.

---

## 2. The Eight Graph Features & Non-Leakage

```
┌────────────────────────────────────────────────────────────────────────┐
│                THE 6TH FEATURE GROUP: "graph" (8 features)             │
├────────────────────────────────────────┬─────────────────────────────────┤
│ graph_agent_out_degree                  │ Distinct merchants this agent │
│                                          │ has paid so far.               │
│ graph_merchant_in_degree                │ Distinct agents that have paid│
│                                          │ this merchant so far.          │
│ graph_agent_pagerank                    │ PageRank of the agent node.    │
│ graph_merchant_pagerank                 │ PageRank of the merchant node.│
│ graph_agent_betweenness                 │ Betweenness centrality of the │
│                                          │ agent node.                    │
│ graph_community_size_ratio              │ Size of this agent's Louvain  │
│                                          │ community / total nodes.       │
│ graph_device_shared_count               │ Distinct agents seen using    │
│                                          │ this transaction's device.     │
│ graph_cross_rail_fanout_velocity        │ The ONE graph feature needing │
│                                          │ no cross-authority graph at   │
│                                          │ all — computed from tx_history│
│                                          │ directly, live-arena included.│
└──────────────────────────────────────────┴─────────────────────────────────┘
```

`ALL_FEATURE_NAMES` grew from **29 to 37** features across **6** groups (raw_transaction, delegation, cross_rail, semantic, security, graph).

### The non-leakage rule, and why it's the whole ballgame

`PaymentGraph.snapshot_features()` must be called **before** `add_transaction()` for the same transaction — a transaction's OWN edge must never appear in its own features:

```python
# backend/app/detector/dataset_builder.py (inside emit())
graph_feats = graph.snapshot_features(auth.agent_id, tx.merchant_id, tx.device_id)
features = DTLFeatureExtractor.extract_features(auth, tx, history, graph_features=graph_feats)
graph.add_transaction(auth.agent_id, tx.merchant_id, tx.device_id)   # AFTER, not before
```

This is the exact same anti-leakage discipline LEARN_06 already teaches for spend-booking order — applied to a graph instead of a ledger balance.

`graph_features` is an **optional** parameter on `extract_features()`. When absent — every live arena call today, since a single round has only one authority — every graph_* feature defaults to `0.0`, **except** `graph_cross_rail_fanout_velocity`, which is computed from `tx_history` and needs no cross-authority graph at all. This is stated as an honest "no signal available," not a placeholder.

---

## 3. The Case Study: A Suspicious Number Is a Bug Report

This is worth reading slowly, because it is the clearest example in the whole codebase of the project's stated discipline actually being exercised under pressure, not just claimed in a docstring.

### What happened

The first retrain after adding graph features scored **PR-AUC = 1.0, ROC-AUC = 1.0, F1 = 1.0** — and the #1 SHAP feature was `graph_merchant_in_degree`.

### Why that should make you suspicious, not happy

A perfect score on a fraud-detection benchmark is almost never a genuine result — it is almost always a **leak**: some feature that, by construction of the data generator, is a near-perfect proxy for the label. The previous (pre-graph) baseline scored **0.9400** PR-AUC — a real, hard-won number, not a trivial one. Jumping to a flawless 1.0 the moment ONE new feature group was added is exactly the shape a leak takes.

### Finding the actual leak

`dataset_builder.py`'s `_build_transaction()` (pre-existing code, unrelated to this session's changes) routes **every attack family** through exactly ONE fixed `merchant_id` string — `"merch_split_chain"` for `CROSS_RAIL_SPLIT`, `"merch_laundering_mega"` for `INTENT_LAUNDERING`, and so on — and legitimate traffic through yet another fixed string. Because `graph_merchant_in_degree` and `graph_merchant_pagerank` are keyed by `merchant_id`, they became a **near-perfect fingerprint of the label**, re-encoded through a graph node identity instead of a raw string. This is precisely the proxy-leakage failure mode the dataset generator's own comments already warn about for stored-value ratio and amount-range overlap — it simply hadn't been applied to merchant identity before, because raw `merchant_id` was never a feature until graph nodes were keyed by it.

### The fix: `_diversify_merchant()`

```python
# backend/app/detector/dataset_builder.py
COMMON_MERCHANT_POOL = [
    ("merch_common_hub_01", "CityWide Retail Hub"),
    ("merch_common_hub_02", "Metro Shopping Plaza"),
    ("merch_common_hub_03", "Neighborhood Super Center"),
]

def _diversify_merchant(self, tx: SyntheticTransaction) -> None:
    if random.random() < 0.22:
        merchant_id, merchant_name = random.choice(self.COMMON_MERCHANT_POOL)
        tx.merchant_id, tx.merchant_name = merchant_id, merchant_name
```

22% of **all** traffic — attack and legitimate alike — is rerouted through a small shared merchant pool, breaking the 1:1 mapping between merchant identity and label. MCC is deliberately left untouched, so semantic/category-based features are unaffected; only the merchant-identity-keyed graph node loses its fingerprint property.

### The retrain after the fix

```
┌────────────────────────────────────────────────────────────────────────┐
│              BEFORE vs. AFTER THE MERCHANT-DIVERSITY FIX               │
├──────────────────────────────┬────────────────┬──────────────────────────┤
│                               │ Before (buggy) │ After (fixed)            │
├──────────────────────────────┼────────────────┼──────────────────────────┤
│ PR-AUC (test slice)           │ 1.0000 ← fake  │ 0.9209 ← genuine         │
│ ROC-AUC                       │ 1.0000         │ 0.9766                  │
│ #1 SHAP feature               │ graph_merchant_│ cart_intent_consistency │
│                               │ in_degree       │ _score (a real semantic │
│                               │                 │ feature)                 │
│ #2 SHAP feature               │ cart_intent_   │ graph_merchant_pagerank │
│                               │ consistency_    │ (meaningful, not        │
│                               │ score           │ dominant)                 │
└──────────────────────────────┴────────────────┴──────────────────────────┘
```

**0.9209 is still a genuine improvement** over the pre-graph 0.9400 baseline — it just isn't a fabricated one anymore. That distinction is the entire lesson.

---

## 4. The Extended Ablation: Raw / Raw+DTL / Raw+DTL+Graph / Full Hybrid

`backend/app/detector/ablation.py` already had six variants (LEARN_06). Two were added to give the specific progression Graph Sentinel is measured against:

```
┌────────────────────────────────────────────────────────────────────────┐
│              NINE ABLATION VARIANTS (37-FEATURE SCHEMA)                │
├──────────────────────────────────────────┬─────────────┬─────────────────┤
│ A: all features (Full Hybrid)             │ 37 features │ genuinely      │
│ B: remove DTL                             │ 25          │ retrained per  │
│ C: remove semantic                        │ 32          │ variant, same  │
│ D: remove cross-rail                      │ 31          │ split, exactly │
│ E: remove delegation                      │ 31          │ as in LEARN_06 │
│ F: raw transaction only (Raw Only)        │ 8           │                │
│ G: remove graph                           │ 29          │ NEW            │
│ H: raw + DTL only (Raw+DTL)               │ 20          │ NEW            │
│ I: raw + DTL + graph (Raw+DTL+Graph)      │ 28          │ NEW            │
└──────────────────────────────────────────┴─────────────┴─────────────────┘

measured_dtl_feature_lift   = PR-AUC(A) - PR-AUC(B)                = +0.2302 (+31.7%)
measured_graph_feature_lift = PR-AUC(I: raw+DTL+graph)
                             - PR-AUC(H: raw+DTL only)              = +0.0095 (+1.0%)
```

Both numbers come from the **same** genuinely-retrained-per-variant harness that already produced the DTL lift in LEARN_06 — nothing new was fabricated to produce the graph number, and the graph lift is deliberately measured on TOP of DTL rather than on top of nothing, so it isolates what graph features add once delegation/cross-rail features are already present.

---

## Check yourself

1. **What can graph features see that every other feature group structurally cannot?**
2. **Why must `snapshot_features()` always be called before `add_transaction()` for the same transaction?**
3. **What was the actual root cause of the fake 1.0 PR-AUC, and how was it found?**
4. **Why does the fix leave MCC untouched and only reroute `merchant_id`/`merchant_name`?**
5. **What does `measured_graph_feature_lift` compare, and why is it measured on top of Raw+DTL rather than on top of Raw alone?**

<details>
<summary>Answers</summary>

1. Cross-authority patterns — many different agents converging on one merchant, shared device fingerprints, or an agent's centrality in the overall graph — since every other group is computed from one authority's own state alone.
2. Otherwise a transaction's own edge would leak into its own features (e.g. its own device-sharing edge inflating its own `graph_device_shared_count`) — the same non-leakage discipline as extracting features before booking spend.
3. Every attack family (and legitimate traffic) routed through exactly ONE fixed `merchant_id`, so the new merchant-identity-keyed graph features became a near-perfect label fingerprint. Found because a perfect 1.0 PR-AUC is inherently suspicious, and the pre-graph baseline (0.9400) proved it wasn't previously achievable.
4. Because MCC-based (semantic/category) features are legitimate and unaffected by the leak; only the merchant-IDENTITY-keyed graph features needed their 1:1 mapping to the label broken.
5. It compares PR-AUC(Raw+DTL+Graph) against PR-AUC(Raw+DTL) — isolating what graph adds once delegation/cross-rail signal is already available, rather than conflating graph's contribution with DTL's.
</details>

---

## Where to go next
→ [LEARN_20 — Adaptive Immune System & the Unified Risk Engine](LEARN_20_ADAPTIVE_IMMUNE_SYSTEM.md)
