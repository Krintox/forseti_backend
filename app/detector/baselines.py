"""
Four-architecture baseline benchmark.

All four models are trained and scored on the SAME temporal split and the SAME
test slice, so the comparison is apples-to-apples. Nothing here is hardcoded.

  1. rules_only     - static per-rail limit rules, no learning at all.
  2. per_rail_ml    - one independent model PER RAIL, each trained only on its
                      own rail's traffic. This is the real-world siloed world:
                      the card model cannot see UPI, and vice versa.
  3. ml_without_dtl - a single global model with transaction + semantic +
                      security features but NO delegation/cross-rail features.
  4. hybrid_dtl_ml  - the full FORSETI feature set including DTL context.

The headline comparison is not overall PR-AUC but recall on CROSS_RAIL_SPLIT,
which is the attack the DTL features exist to catch.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from .dataset_builder import SyntheticMLDatasetBuilder
from .feature_schema import ALL_FEATURE_NAMES, FEATURE_GROUPS
from .model import backend_info, build_classifier, compute_scale_pos_weight
from .train import DEFAULT_HOLDOUT_FAMILIES, evaluate_scores

# Feature groups that encode global delegation context. Removing these is what
# "without DTL" means.
DTL_GROUPS = ["delegation", "cross_rail"]
DTL_FEATURES = [f for g in DTL_GROUPS for f in FEATURE_GROUPS[g]]
NON_DTL_FEATURES = [f for f in ALL_FEATURE_NAMES if f not in DTL_FEATURES]

# A per-rail silo additionally cannot know the rail identity of other rails.
PER_RAIL_FEATURES = [f for f in NON_DTL_FEATURES if f != "rail_code"]

RAIL_CODES = {0.0: "CARD", 1.0: "UPI", 2.0: "AGENTIC"}


def _measure_latency(predict_fn, X: np.ndarray, n: int = 300) -> List[float]:
    """Per-transaction wall-clock latency in milliseconds."""
    lat: List[float] = []
    for row in X[:n]:
        t0 = time.perf_counter()
        predict_fn(row.reshape(1, -1))
        lat.append((time.perf_counter() - t0) * 1000.0)
    return lat or [0.0]


def _latency_block(lat: List[float]) -> Dict[str, float]:
    arr = np.asarray(lat, dtype=float)
    return {
        "p50_ms": round(float(np.percentile(arr, 50)), 4),
        "p95_ms": round(float(np.percentile(arr, 95)), 4),
        "p99_ms": round(float(np.percentile(arr, 99)), 4),
        "mean_ms": round(float(arr.mean()), 4),
        "n_measured": int(len(arr)),
    }


def _per_family_recall(test_df: pd.DataFrame, probs: np.ndarray, threshold: float = 0.5) -> Dict[str, Any]:
    """Recall broken out per attack family - this is where DTL lift shows up."""
    out: Dict[str, Any] = {}
    for fam in sorted(f for f in test_df["attack_family"].unique() if f != "NONE"):
        mask = (test_df["attack_family"] == fam).values
        if mask.sum() == 0:
            continue
        out[fam] = {
            "n": int(mask.sum()),
            "recall": round(float((probs[mask] >= threshold).mean()), 4),
            "mean_score": round(float(probs[mask].mean()), 4),
        }
    return out


def run_baseline_condition(
    seed: int = 42,
    num_samples: int = 20000,
    fraud_ratio: float = 0.012,
    holdout_families: Optional[List[str]] = None,
    output_path: Optional[str] = None,
) -> Dict[str, Any]:
    info = backend_info()
    print("=" * 74)
    print(f"FOUR-ARCHITECTURE BASELINE BENCHMARK  |  seed={seed}  |  backend={info['backend']}")
    print("=" * 74)

    holdouts = DEFAULT_HOLDOUT_FAMILIES if holdout_families is None else holdout_families
    builder = SyntheticMLDatasetBuilder(seed=seed)
    df = builder.generate_trajectory(num_samples=num_samples, fraud_ratio=fraud_ratio, holdout_families=holdouts)

    n = len(df)
    train_end, val_end = int(n * 0.70), int(n * 0.85)
    train_df, test_df = df.iloc[:train_end].copy(), df.iloc[val_end:].copy()
    train_fit_df = train_df[train_df["is_holdout"] == 0].copy()

    y_train = train_fit_df["is_fraud"].values
    y_test = test_df["is_fraud"].values
    amounts = test_df["amount"].values
    spw = compute_scale_pos_weight(y_train)

    print(f"  train={len(train_fit_df)}  test={len(test_df)}  test prevalence={y_test.mean():.4f}")
    print(f"  attack families held out of training: {holdouts}")

    results: Dict[str, Any] = {
        "metadata": {
            "experiment_id": f"BASELINE-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "seed": seed,
            "model_backend": info,
            "attack_families_held_out": holdouts,
            "sample_counts": {"train": int(len(train_fit_df)), "test": int(len(test_df))},
            "shared_test_slice": True,
            "dtl_feature_names": DTL_FEATURES,
        },
        "baselines": {},
    }

    # ------------------------------------------------------- 1. rules only
    print("\n[1/4] Rules-only (static per-rail limits, no learning)")
    rule_lat: List[float] = []
    rule_probs = np.zeros(len(test_df), dtype=float)
    for i, (_, row) in enumerate(test_df.iterrows()):
        t0 = time.perf_counter()
        # A conventional per-rail control: flag large tickets and merchant
        # categories outside the delegated scope. It has no global view.
        score = 0.05
        if row["amount"] > 3500.0:
            score = max(score, 0.85)
        if row["merchant_category_match_bool"] == 0.0:
            score = max(score, 0.80)
        rule_lat.append((time.perf_counter() - t0) * 1000.0)
        rule_probs[i] = score
    block = evaluate_scores(y_test, rule_probs, amounts)
    block.update({"model_variant": "Rules-only (static per-rail limits)",
                  "feature_count": 0, "latency": _latency_block(rule_lat),
                  "per_family_recall": _per_family_recall(test_df, rule_probs)})
    results["baselines"]["rules_only"] = block
    print(f"  PR-AUC={block['pr_auc']}  recall={block['recall']}  FPR={block['false_positive_rate']}")

    # ----------------------------------------------------- 2. per-rail ML
    print("\n[2/4] Per-rail ML (independent model per rail, no cross-rail view)")
    per_rail_probs = np.zeros(len(test_df), dtype=float)
    per_rail_lat: List[float] = []
    rails_trained: Dict[str, Any] = {}
    test_rail_codes = test_df["rail_code"].values
    train_rail_codes = train_fit_df["rail_code"].values

    for code, rail_name in RAIL_CODES.items():
        tr_mask, te_mask = train_rail_codes == code, test_rail_codes == code
        if tr_mask.sum() < 50 or te_mask.sum() == 0:
            rails_trained[rail_name] = {"status": "INSUFFICIENT DATA", "train_rows": int(tr_mask.sum())}
            continue
        y_tr = y_train[tr_mask]
        if len(np.unique(y_tr)) < 2:
            rails_trained[rail_name] = {"status": "SINGLE CLASS IN TRAIN", "train_rows": int(tr_mask.sum())}
            continue
        X_tr = train_fit_df.loc[tr_mask, PER_RAIL_FEATURES].values
        X_te = test_df.loc[te_mask, PER_RAIL_FEATURES].values
        m = build_classifier(seed=seed, scale_pos_weight=compute_scale_pos_weight(y_tr), n_estimators=200)
        m.fit(X_tr, y_tr)
        per_rail_probs[te_mask] = m.predict_proba(X_te)[:, 1]
        per_rail_lat.extend(_measure_latency(lambda r: m.predict_proba(r), X_te, n=100))
        rails_trained[rail_name] = {"status": "TRAINED", "train_rows": int(tr_mask.sum()), "test_rows": int(te_mask.sum())}

    block = evaluate_scores(y_test, per_rail_probs, amounts)
    block.update({"model_variant": "Per-rail ML (siloed, one model per rail)",
                  "feature_count": len(PER_RAIL_FEATURES), "rails": rails_trained,
                  "latency": _latency_block(per_rail_lat or [0.0]),
                  "per_family_recall": _per_family_recall(test_df, per_rail_probs)})
    results["baselines"]["per_rail_ml"] = block
    print(f"  PR-AUC={block['pr_auc']}  recall={block['recall']}  FPR={block['false_positive_rate']}")

    # -------------------------------------------------- 3. ML without DTL
    print(f"\n[3/4] Global ML without DTL features ({len(NON_DTL_FEATURES)} features)")
    m_nodtl = build_classifier(seed=seed, scale_pos_weight=spw)
    m_nodtl.fit(train_fit_df[NON_DTL_FEATURES].values, y_train)
    X_te_nodtl = test_df[NON_DTL_FEATURES].values
    probs_nodtl = m_nodtl.predict_proba(X_te_nodtl)[:, 1]
    block = evaluate_scores(y_test, probs_nodtl, amounts)
    block.update({"model_variant": "Global ML without DTL features",
                  "feature_count": len(NON_DTL_FEATURES),
                  "latency": _latency_block(_measure_latency(lambda r: m_nodtl.predict_proba(r), X_te_nodtl)),
                  "per_family_recall": _per_family_recall(test_df, probs_nodtl)})
    results["baselines"]["ml_without_dtl"] = block
    print(f"  PR-AUC={block['pr_auc']}  recall={block['recall']}  FPR={block['false_positive_rate']}")

    # ------------------------------------------------- 4. full hybrid DTL
    print(f"\n[4/4] FORSETI hybrid DTL+ML ({len(ALL_FEATURE_NAMES)} features)")
    m_full = build_classifier(seed=seed, scale_pos_weight=spw)
    m_full.fit(train_fit_df[ALL_FEATURE_NAMES].values, y_train)
    X_te_full = test_df[ALL_FEATURE_NAMES].values
    probs_full = m_full.predict_proba(X_te_full)[:, 1]
    block = evaluate_scores(y_test, probs_full, amounts)
    block.update({"model_variant": "FORSETI hybrid (DTL features + GBDT)",
                  "feature_count": len(ALL_FEATURE_NAMES),
                  "latency": _latency_block(_measure_latency(lambda r: m_full.predict_proba(r), X_te_full)),
                  "per_family_recall": _per_family_recall(test_df, probs_full)})
    results["baselines"]["hybrid_dtl_ml"] = block
    print(f"  PR-AUC={block['pr_auc']}  recall={block['recall']}  FPR={block['false_positive_rate']}")

    # ------------------------------------- 5. deterministic DTL invariant
    # No learning at all. This evaluates the exact predicate the runtime
    # DTLInvariantEngine enforces:
    #   INV_01  aggregate exposure after this tx exceeds the delegated ceiling
    #   INV_02  cart contains stored-value / semantically excluded items
    #   INV_03  merchant category outside the delegated scope
    # It is included because the flagship claim is about this engine, not about
    # the classifier, and a claim that is never measured is not evidence.
    print("\n[5/5] Deterministic DTL invariant engine (no ML)")
    inv_lat: List[float] = []
    inv_probs = np.zeros(len(test_df), dtype=float)
    exposure_after = test_df["exposure_after_tx_ratio"].values
    stored_value = test_df["stored_value_item_count"].values
    mcc_match = test_df["merchant_category_match_bool"].values
    for i in range(len(test_df)):
        t0 = time.perf_counter()
        violated = (exposure_after[i] > 1.0) or (stored_value[i] > 0.0) or (mcc_match[i] == 0.0)
        inv_lat.append((time.perf_counter() - t0) * 1000.0)
        inv_probs[i] = 1.0 if violated else 0.0
    block = evaluate_scores(y_test, inv_probs, amounts)
    block.update({"model_variant": "Deterministic DTL invariant engine (no ML)",
                  "feature_count": 3, "is_deterministic": True,
                  "invariants": ["INV_01_GLOBAL_BUDGET_EXCEEDED",
                                 "INV_02_SEMANTIC_INTENT_DRIFT",
                                 "INV_03_UNAUTHORIZED_MCC"],
                  "latency": _latency_block(inv_lat),
                  "per_family_recall": _per_family_recall(test_df, inv_probs)})
    results["baselines"]["dtl_invariant_only"] = block
    print(f"  PR-AUC={block['pr_auc']}  recall={block['recall']}  FPR={block['false_positive_rate']}")
    xr = block["per_family_recall"].get("CROSS_RAIL_SPLIT", {}).get("recall")
    print(f"  CROSS_RAIL_SPLIT recall={xr} (deterministic, holdout-independent)")

    # ------------------------------------------------------- measured lift
    full_pr = results["baselines"]["hybrid_dtl_ml"]["pr_auc"]
    nodtl_pr = results["baselines"]["ml_without_dtl"]["pr_auc"]
    per_rail_pr = results["baselines"]["per_rail_ml"]["pr_auc"]

    def _fam_recall(variant: str, fam: str) -> Optional[float]:
        return results["baselines"][variant].get("per_family_recall", {}).get(fam, {}).get("recall")

    results["measured_dtl_lift"] = {
        "definition": "PR-AUC(hybrid with DTL features) - PR-AUC(same model without DTL features), same test slice",
        "pr_auc_hybrid_dtl": full_pr,
        "pr_auc_without_dtl": nodtl_pr,
        "pr_auc_lift": round(full_pr - nodtl_pr, 4),
        "pr_auc_lift_vs_per_rail_silo": round(full_pr - per_rail_pr, 4),
        "cross_rail_split_recall": {
            "rules_only": _fam_recall("rules_only", "CROSS_RAIL_SPLIT"),
            "per_rail_ml": _fam_recall("per_rail_ml", "CROSS_RAIL_SPLIT"),
            "ml_without_dtl": _fam_recall("ml_without_dtl", "CROSS_RAIL_SPLIT"),
            "hybrid_dtl_ml": _fam_recall("hybrid_dtl_ml", "CROSS_RAIL_SPLIT"),
            "dtl_invariant_only": _fam_recall("dtl_invariant_only", "CROSS_RAIL_SPLIT"),
            "note": "Every learned model scores each transaction in isolation, so a cross-rail leg "
                    "looks ordinary to it. The deterministic DTL invariant compares AGGREGATE exposure "
                    "against the delegated ceiling and is therefore holdout-independent: it does not "
                    "need to have seen the attack family before.",
        },
    }
    print(f"\n  measured DTL PR-AUC lift: {results['measured_dtl_lift']['pr_auc_lift']:+.4f}")

    if output_path:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2)
        print(f"  saved -> {output_path}")
    print("=" * 74)
    return results




def wilson_interval(successes: int, n: int, z: float = 1.96) -> Tuple[float, float]:
    """
    95% Wilson score interval for a binomial proportion.

    Wilson rather than the normal approximation because n here is 64 and the
    proportions run close to 0 and 1, where the normal interval is badly wrong
    (it happily returns bounds above 1.0). Wilson stays inside [0, 1] and is the
    standard recommendation for exactly this regime.
    """
    if n <= 0:
        return (0.0, 0.0)
    phat = successes / n
    denom = 1.0 + z * z / n
    centre = (phat + z * z / (2 * n)) / denom
    margin = (z / denom) * ((phat * (1 - phat) / n + z * z / (4 * n * n)) ** 0.5)
    return (round(max(0.0, centre - margin), 4), round(min(1.0, centre + margin), 4))


def recall_with_ci(entry: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Attaches a 95% CI to a per-family recall, so the number can be read honestly."""
    if not entry or entry.get("recall") is None or not entry.get("n"):
        return None
    n = int(entry["n"])
    recall = float(entry["recall"])
    successes = int(round(recall * n))
    low, high = wilson_interval(successes, n)
    return {
        "recall": round(recall, 4),
        "n": n,
        "caught": successes,
        "ci95": [low, high],
        "ci95_halfwidth": round((high - low) / 2, 4),
    }


def _overlap(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    """True when two intervals overlap - i.e. the difference is not resolvable."""
    if not a or not b:
        return True
    return a["ci95"][0] <= b["ci95"][1] and b["ci95"][0] <= a["ci95"][1]


def _headline_claim(held_out: Dict[str, Any], seen: Dict[str, Any],
                    ci: Optional[Dict[str, Any]] = None) -> str:
    """
    Writes the headline sentence FROM the measured recalls, and refuses to claim
    a difference the sample size cannot support.

    The interesting property is not "ML fails" - with a cross-rail feature and
    the family in training, a classifier does fine. It is that the invariant is
    holdout-independent: its held-out and seen numbers are the SAME number,
    because it is arithmetic over the grant rather than a fitted decision
    surface.

    The cross-rail slice is n=64. At that size a 95% Wilson interval on a recall
    near 0.83 is roughly +/-0.09, so a gap of 0.016 between held-out and seen is
    inside the noise and saying "it generalised, within 0.016" would be
    overclaiming on our own data. The sentence below says only what n supports,
    and names n so the reader can check.
    """
    def g(table: Dict[str, Any], key: str) -> Optional[float]:
        value = table.get(key)
        return round(float(value), 4) if isinstance(value, (int, float)) else None

    inv_out, inv_seen = g(held_out, "dtl_invariant_only"), g(seen, "dtl_invariant_only")
    ml_out, ml_seen = g(held_out, "hybrid_dtl_ml"), g(seen, "hybrid_dtl_ml")
    blind_out = g(held_out, "ml_without_dtl")

    if None in (inv_out, inv_seen, ml_out, ml_seen, blind_out):
        return "Cross-rail recall could not be computed for this run."

    holdout_independent = abs(inv_out - inv_seen) < 1e-9

    n = None
    ml_gap_resolvable = None
    ml_beats_blind = None
    if ci:
        ml_o, ml_s = ci.get("held_out", {}).get("hybrid_dtl_ml"), ci.get("seen", {}).get("hybrid_dtl_ml")
        blind_o = ci.get("held_out", {}).get("ml_without_dtl")
        if ml_o:
            n = ml_o["n"]
        if ml_o and ml_s:
            ml_gap_resolvable = not _overlap(ml_o, ml_s)
        if ml_o and blind_o:
            ml_beats_blind = not _overlap(ml_o, blind_o)

    parts = [
        f"The deterministic DTL aggregate-authority invariant scores {inv_out:.4f} on "
        f"CROSS_RAIL_SPLIT whether or not the family was in training "
        f"({inv_seen:.4f} seen), because it is arithmetic over the delegated grant"
        + (" - holdout-independent by construction, so this is an identity rather than "
           "a measurement that happened to come out equal."
           if holdout_independent
           else ", and this run measured a difference between the two, which should not happen."),
    ]

    blind_sentence = (
        f"A model WITHOUT a cross-rail view reaches only {blind_out:.4f} held out: one leg "
        f"genuinely looks like ordinary spending, so the signal is not in the transaction."
    )
    if ml_beats_blind is True:
        blind_sentence += (
            f" That gap against hybrid ML's {ml_out:.4f} is wider than the 95% intervals at "
            f"n={n}, so it is a real separation and not sampling noise."
        )
    elif ml_beats_blind is False:
        blind_sentence += (
            f" At n={n} the 95% intervals still overlap hybrid ML's {ml_out:.4f}, so this run "
            f"does not establish the separation on its own."
        )
    parts.append(blind_sentence)

    ml_sentence = (
        f"Given DTL aggregate features, hybrid ML reaches {ml_out:.4f} held out against "
        f"{ml_seen:.4f} seen"
    )
    if ml_gap_resolvable is False:
        ml_sentence += (
            f" - a difference of {abs(ml_seen - ml_out):.4f} that the 95% intervals at n={n} "
            f"cannot resolve. The honest reading is that this run does not show the classifier "
            f"degrading on an unseen family; it does NOT show that it never would, and a "
            f"held-out result this close should not be presented as proven generalisation."
        )
    elif ml_gap_resolvable is True:
        ml_sentence += (
            f" - a gap the 95% intervals at n={n} do resolve, so the classifier measurably "
            f"lost ground on the family it had not seen."
        )
    else:
        ml_sentence += "."
    parts.append(ml_sentence)

    parts.append(
        "The asymmetry is the point and it does not depend on the sample size: the invariant's "
        "two numbers are equal BY CONSTRUCTION, while the classifier's are two separate "
        "measurements that happen to be close on this run."
    )
    return " ".join(parts)


def run_baseline_experiments(
    seed: int = 42,
    num_samples: int = 20000,
    fraud_ratio: float = 0.012,
    output_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Runs the four-architecture benchmark under BOTH evaluation conditions,
    because each answers a different question and neither alone is sufficient.

    Condition A - attack-family holdout (CROSS_RAIL_SPLIT, REVOCATION_FLOOD are
      never seen in training). Answers: "does the learned model generalise to an
      attack family it has never seen?" This is the harder, more honest test.

    Condition B - all families seen in training. Answers: "given the chance to
      learn this attack, do the DTL features actually help?" This is where the
      DTL feature lift is measurable; under condition A no model can learn the
      held-out family, so a lift there would be meaningless.
    """
    cond_a = run_baseline_condition(seed=seed, num_samples=num_samples, fraud_ratio=fraud_ratio,
                                    holdout_families=DEFAULT_HOLDOUT_FAMILIES)
    print()
    cond_b = run_baseline_condition(seed=seed, num_samples=num_samples, fraud_ratio=fraud_ratio,
                                    holdout_families=[])

    # 95% Wilson intervals for the cross-rail slice, per architecture and per
    # condition. `_fam_ci` reaches back into the raw per-family counts rather
    # than the rounded recall so `caught` is exact.
    def _fam_ci(condition: Dict[str, Any]) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for variant in ("rules_only", "per_rail_ml", "ml_without_dtl",
                        "hybrid_dtl_ml", "dtl_invariant_only"):
            entry = (condition["baselines"].get(variant, {})
                     .get("per_family_recall", {}).get("CROSS_RAIL_SPLIT"))
            band = recall_with_ci(entry)
            if band is not None:
                out[variant] = band
        return out

    cross_rail_ci = {
        "method": "Wilson score interval, 95%",
        "why": ("The cross-rail slice is small. A point estimate alone invites a "
                "comparison between two architectures that the sample size cannot "
                "support, so the interval ships with the number."),
        "held_out": _fam_ci(cond_a),
        "seen": _fam_ci(cond_b),
    }

    combined = {
        "metadata": {
            "experiment_id": f"BASELINE-SUITE-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "seed": seed,
            "model_backend": backend_info(),
            "conditions": {
                "attack_family_holdout": "CROSS_RAIL_SPLIT and REVOCATION_FLOOD removed from training",
                "all_families_seen": "no attack family withheld",
            },
        },
        "condition_attack_family_holdout": cond_a,
        "condition_all_families_seen": cond_b,
        # Kept at the top level so existing API consumers still resolve.
        "baselines": cond_a["baselines"],
        "measured_dtl_lift": cond_b["measured_dtl_lift"],
        "headline_finding": {
            # DERIVED from the numbers beside it, never asserted.
            #
            # This field used to be a hardcoded sentence - "per-transaction ML
            # cannot detect cross-rail splitting" - that stayed word for word
            # identical no matter what the run measured. When the MCC leak was
            # fixed and hybrid ML went from 0.000 to 0.828 held out, the claim
            # did not move, and the artifact ended up asserting something its
            # own data contradicted. Adversarial review found that before we
            # did. A sentence generated from the measurement cannot drift from
            # it.
            "claim": _headline_claim(
                cond_a["measured_dtl_lift"]["cross_rail_split_recall"],
                cond_b["measured_dtl_lift"]["cross_rail_split_recall"],
                ci=cross_rail_ci,
            ),
            "cross_rail_split_recall_when_family_held_out": cond_a["measured_dtl_lift"]["cross_rail_split_recall"],
            "cross_rail_split_recall_when_family_seen": cond_b["measured_dtl_lift"]["cross_rail_split_recall"],
            # The cross-rail slice is small (n is reported per row). Publishing
            # the point estimate alone invited a comparison the sample size does
            # not support, so the intervals ship with it.
            "cross_rail_split_recall_ci95": cross_rail_ci,
            "dtl_pr_auc_lift_when_family_seen": cond_b["measured_dtl_lift"]["pr_auc_lift"],
        },
    }

    if output_path is None:
        from ..paths import BASELINES_PATH

        output_path = BASELINES_PATH
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(combined, f, indent=2)
    print(f"\nBaseline suite saved -> {output_path}")
    return combined


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run FORSETI baseline benchmark")
    parser.add_argument("--seed", type=int, default=int(os.environ.get("SEED", 42)))
    parser.add_argument("--samples", type=int, default=20000)
    args = parser.parse_args()
    run_baseline_experiments(seed=args.seed, num_samples=args.samples)
