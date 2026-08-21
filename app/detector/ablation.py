"""
Feature-group ablation study.

Each variant is genuinely retrained on the same temporal split and scored on the
same test slice. No number here is predetermined; the "DTL feature lift" the UI
shows is computed as the measured difference between variant A and variant B.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

import numpy as np  # noqa: E402

from .dataset_builder import SyntheticMLDatasetBuilder  # noqa: E402
from .feature_schema import ALL_FEATURE_NAMES, FEATURE_GROUPS  # noqa: E402
from .model import backend_info, build_classifier, compute_scale_pos_weight  # noqa: E402
from .train import DEFAULT_HOLDOUT_FAMILIES, evaluate_scores  # noqa: E402

DTL_ONLY_GROUPS = ["delegation", "cross_rail"]


def _without(*groups: str) -> List[str]:
    drop = {f for g in groups for f in FEATURE_GROUPS[g]}
    return [f for f in ALL_FEATURE_NAMES if f not in drop]


def _only(*groups: str) -> List[str]:
    keep = {f for g in groups for f in FEATURE_GROUPS[g]}
    return [f for f in ALL_FEATURE_NAMES if f in keep]


def build_variants() -> List[Dict[str, Any]]:
    """
    The six original ablation variants, plus H/I: the specific "Raw Only /
    Raw+DTL / Raw+DTL+Graph / Full Hybrid" progression the Graph Sentinel
    module is measured against. F already covers Raw Only and A already
    covers Full Hybrid, so only the two intermediate points needed adding.
    """
    return [
        {"id": "A_all_features", "name": "A: all features (Full Hybrid)", "features": list(ALL_FEATURE_NAMES)},
        {"id": "B_no_dtl", "name": "B: remove DTL (delegation + cross-rail)", "features": _without(*DTL_ONLY_GROUPS)},
        {"id": "C_no_semantic", "name": "C: remove semantic", "features": _without("semantic")},
        {"id": "D_no_cross_rail", "name": "D: remove cross-rail", "features": _without("cross_rail")},
        {"id": "E_no_delegation", "name": "E: remove delegation", "features": _without("delegation")},
        {"id": "F_raw_only", "name": "F: raw transaction features only (Raw Only)", "features": list(FEATURE_GROUPS["raw_transaction"])},
        {"id": "G_no_graph", "name": "G: remove graph", "features": _without("graph")},
        {"id": "H_raw_plus_dtl", "name": "H: raw + DTL only (Raw+DTL)", "features": _only("raw_transaction", *DTL_ONLY_GROUPS)},
        {"id": "I_raw_plus_dtl_plus_graph", "name": "I: raw + DTL + graph (Raw+DTL+Graph)",
         "features": _only("raw_transaction", *DTL_ONLY_GROUPS, "graph")},
    ]


def run_ablation_experiments(
    seed: int = 42,
    num_samples: int = 24000,
    fraud_ratio: float = 0.012,
    holdout_families: Optional[List[str]] = None,
    output_path: Optional[str] = None,
) -> Dict[str, Any]:
    info = backend_info()
    print("=" * 74)
    print(f"FEATURE-GROUP ABLATION  |  seed={seed}  |  backend={info['backend']}")
    print("=" * 74)

    holdouts = DEFAULT_HOLDOUT_FAMILIES if holdout_families is None else holdout_families
    builder = SyntheticMLDatasetBuilder(seed=seed)
    df = builder.generate_trajectory(num_samples=num_samples, fraud_ratio=fraud_ratio, holdout_families=holdouts)

    n = len(df)
    train_end, val_end = int(n * 0.70), int(n * 0.85)
    train_df, test_df = df.iloc[:train_end].copy(), df.iloc[val_end:].copy()
    train_fit_df = train_df[train_df["is_holdout"] == 0].copy()

    y_train, y_test = train_fit_df["is_fraud"].values, test_df["is_fraud"].values
    amounts = test_df["amount"].values
    spw = compute_scale_pos_weight(y_train)

    print(f"  train={len(train_fit_df)}  test={len(test_df)}  test prevalence={y_test.mean():.4f}")
    print(f"  attack families held out of training: {holdouts}\n")

    results: Dict[str, Any] = {
        "metadata": {
            "experiment_id": f"ABLATION-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "seed": seed,
            "model_backend": info,
            "attack_families_held_out": holdouts,
            "sample_counts": {"train": int(len(train_fit_df)), "test": int(len(test_df))},
            "note": "Every variant is retrained from scratch on the identical temporal split.",
        },
        "variants": {},
    }

    for variant in build_variants():
        feats = variant["features"]
        model = build_classifier(seed=seed, scale_pos_weight=spw)
        model.fit(train_fit_df[feats].values, y_train)
        probs = model.predict_proba(test_df[feats].values)[:, 1]

        block = evaluate_scores(y_test, probs, amounts)
        block.update({
            "variant_name": variant["name"],
            "feature_count": len(feats),
            "features_removed": [f for f in ALL_FEATURE_NAMES if f not in feats],
        })
        results["variants"][variant["id"]] = block
        print(f"  {variant['name']:<44} features={len(feats):<3} PR-AUC={block['pr_auc']:.4f} ROC-AUC={block['roc_auc']:.4f}")

    full = results["variants"]["A_all_features"]["pr_auc"]
    no_dtl = results["variants"]["B_no_dtl"]["pr_auc"]
    results["measured_dtl_feature_lift"] = {
        "definition": "PR-AUC(A: all features) - PR-AUC(B: DTL feature groups removed)",
        "pr_auc_all_features": full,
        "pr_auc_without_dtl": no_dtl,
        "lift": round(full - no_dtl, 4),
        "relative_lift_pct": round(((full - no_dtl) / no_dtl * 100.0) if no_dtl > 0 else 0.0, 2),
    }
    print(f"\n  measured DTL feature lift: {results['measured_dtl_feature_lift']['lift']:+.4f} PR-AUC "
          f"({results['measured_dtl_feature_lift']['relative_lift_pct']:+.2f}%)")

    raw_dtl = results["variants"]["H_raw_plus_dtl"]["pr_auc"]
    raw_dtl_graph = results["variants"]["I_raw_plus_dtl_plus_graph"]["pr_auc"]
    results["measured_graph_feature_lift"] = {
        "definition": "PR-AUC(I: raw+DTL+graph) - PR-AUC(H: raw+DTL only)",
        "pr_auc_raw_plus_dtl": raw_dtl,
        "pr_auc_raw_plus_dtl_plus_graph": raw_dtl_graph,
        "lift": round(raw_dtl_graph - raw_dtl, 4),
        "relative_lift_pct": round(((raw_dtl_graph - raw_dtl) / raw_dtl * 100.0) if raw_dtl > 0 else 0.0, 2),
    }
    print(f"  measured graph feature lift: {results['measured_graph_feature_lift']['lift']:+.4f} PR-AUC "
          f"({results['measured_graph_feature_lift']['relative_lift_pct']:+.2f}%)")

    from ..paths import EVALUATION_DIR

    base_dir = EVALUATION_DIR
    os.makedirs(base_dir, exist_ok=True)
    if output_path is None:
        output_path = os.path.join(base_dir, "ablation_results.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    _plot_ablation(results, os.path.join(base_dir, "ablation_pr_auc.png"))
    print(f"  saved -> {output_path}")
    print("=" * 74)
    return results


def _plot_ablation(results: Dict[str, Any], path: str) -> None:
    """PR-AUC vs feature group chart."""
    try:
        items = list(results["variants"].items())
        labels = [v["variant_name"] for _, v in items]
        values = [v["pr_auc"] for _, v in items]
        colors = ["#2563eb" if k == "A_all_features" else "#94a3b8" for k, _ in items]

        fig, ax = plt.subplots(figsize=(8, 4.5))
        bars = ax.barh(labels, values, color=colors)
        ax.set_xlabel("PR-AUC (temporal test slice)")
        ax.set_title("Ablation: PR-AUC by feature group")
        ax.set_xlim(0, max(1.0, max(values) * 1.15))
        for bar, val in zip(bars, values):
            ax.text(val + 0.01, bar.get_y() + bar.get_height() / 2, f"{val:.4f}", va="center", fontsize=9)
        ax.invert_yaxis()
        ax.grid(True, axis="x", linestyle="--", alpha=0.4)
        plt.tight_layout()
        plt.savefig(path, dpi=140)
        plt.close()
    except Exception as exc:
        print(f"  warning: ablation plot failed: {exc}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run FORSETI feature-group ablation")
    parser.add_argument("--seed", type=int, default=int(os.environ.get("SEED", 42)))
    parser.add_argument("--samples", type=int, default=24000)
    args = parser.parse_args()
    run_ablation_experiments(seed=args.seed, num_samples=args.samples)
