"""
FORSETI hybrid detector training pipeline.

Every number this script prints or writes is computed from the run itself.
Nothing is hardcoded. Run with:

    python -m app.detector.train --seed 42 --samples 12000
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import joblib
import numpy as np
import pandas as pd

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from sklearn.metrics import (  # noqa: E402
    average_precision_score,
    confusion_matrix,
    f1_score,
    precision_recall_curve,
    precision_score,
    recall_score,
    roc_auc_score,
    roc_curve,
)

from .calibration import ProbabilityCalibrator, calibration_summary  # noqa: E402
from .dataset_builder import SyntheticMLDatasetBuilder  # noqa: E402
from .explainability import FeatureExplainer  # noqa: E402
from .feature_schema import ALL_FEATURE_NAMES, DTLFeatureExtractor  # noqa: E402
from .model import backend_info, build_classifier, compute_scale_pos_weight  # noqa: E402

DEFAULT_HOLDOUT_FAMILIES = ["CROSS_RAIL_SPLIT", "REVOCATION_FLOOD"]


def calculate_recall_at_fpr(y_true, y_scores, target_fpr: float = 0.005) -> float:
    """Max recall achievable while holding false-positive rate <= target_fpr."""
    y_true = np.asarray(y_true)
    if len(np.unique(y_true)) < 2:
        return 0.0
    fpr, tpr, _ = roc_curve(y_true, y_scores)
    valid = np.where(fpr <= target_fpr)[0]
    if len(valid) == 0:
        return 0.0
    return float(tpr[valid[-1]])


def calculate_precision_at_k(y_true, y_scores, k: int = 100) -> float:
    """Precision among the k highest-scored transactions."""
    y_true = np.asarray(y_true)
    k = min(k, len(y_scores))
    if k == 0:
        return 0.0
    top = np.argsort(y_scores)[::-1][:k]
    return float(np.mean(y_true[top]))


def financial_impact(y_true, y_pred, amounts) -> Dict[str, float]:
    """Measured currency value caught vs. wrongly blocked on the test slice."""
    y_true, y_pred, amounts = np.asarray(y_true), np.asarray(y_pred), np.asarray(amounts, dtype=float)
    caught = amounts[(y_true == 1) & (y_pred == 1)].sum()
    blocked = amounts[(y_true == 0) & (y_pred == 1)].sum()
    missed = amounts[(y_true == 1) & (y_pred == 0)].sum()
    return {
        "fraud_value_caught_inr": round(float(caught), 2),
        "legitimate_value_blocked_inr": round(float(blocked), 2),
        "fraud_value_missed_inr": round(float(missed), 2),
        "net_value_saved_inr": round(float(caught - blocked), 2),
    }


def evaluate_scores(y_true, y_prob, amounts, threshold: float = 0.5) -> Dict[str, Any]:
    """Full metric block for a set of predicted probabilities."""
    y_true = np.asarray(y_true)
    y_pred = (np.asarray(y_prob) >= threshold).astype(int)
    tn, fp, fn, tp = confusion_matrix(y_true, y_pred, labels=[0, 1]).ravel()
    return {
        "pr_auc": round(float(average_precision_score(y_true, y_prob)), 4),
        "roc_auc": round(float(roc_auc_score(y_true, y_prob)), 4),
        "precision": round(float(precision_score(y_true, y_pred, zero_division=0)), 4),
        "recall": round(float(recall_score(y_true, y_pred, zero_division=0)), 4),
        "f1_score": round(float(f1_score(y_true, y_pred, zero_division=0)), 4),
        "recall_at_0.5pct_fpr": round(calculate_recall_at_fpr(y_true, y_prob, 0.005), 4),
        "precision_at_100": round(calculate_precision_at_k(y_true, y_prob, 100), 4),
        "false_positive_rate": round(float(fp / max(1, fp + tn)), 5),
        "confusion_matrix": {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)},
        "financial_impact": financial_impact(y_true, y_pred, amounts),
        "threshold": threshold,
        "n_samples": int(len(y_true)),
        "n_positives": int(y_true.sum()),
    }


def environment_metadata(seed: int) -> Dict[str, Any]:
    """Captured so any run can be reproduced and audited."""
    versions: Dict[str, str] = {}
    for mod in ("numpy", "pandas", "sklearn", "scipy", "xgboost", "lightgbm", "shap", "matplotlib"):
        try:
            versions[mod] = __import__(mod).__version__
        except Exception:
            versions[mod] = "NOT INSTALLED"
    return {
        "seed": seed,
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "package_versions": versions,
        "feature_schema_version": "2.2.0",
    }


def train_pipeline(
    seed: int = 42,
    num_samples: int = 12000,
    fraud_ratio: float = 0.012,
    holdout_families: Optional[List[str]] = None,
    output_dir: Optional[str] = None,
) -> Dict[str, Any]:
    print("=" * 74)
    info = backend_info()
    print(f"FORSETI DETECTOR TRAINING  |  seed={seed}  |  backend={info['backend']} {info['backend_version']}")
    if not info["is_preferred_backend"]:
        print(f"  NOTE: preferred backend XGBoost unavailable; using {info['architecture']}")
    print("=" * 74)

    started = time.time()
    holdouts = DEFAULT_HOLDOUT_FAMILIES if holdout_families is None else holdout_families

    # ---------------------------------------------------------------- dataset
    print(f"\n[1/7] Generating synthetic trajectory ({num_samples} samples, target fraud {fraud_ratio*100:.1f}%)")
    builder = SyntheticMLDatasetBuilder(seed=seed)
    df = builder.generate_trajectory(num_samples=num_samples, fraud_ratio=fraud_ratio, holdout_families=holdouts)

    family_counts = df["attack_family"].value_counts().to_dict()
    print(f"  samples={len(df)}  fraud={int(df['is_fraud'].sum())} ({df['is_fraud'].mean()*100:.2f}%)  features={len(ALL_FEATURE_NAMES)}")
    print(f"  attack families: {family_counts}")

    # ------------------------------------------------------- temporal split
    print("\n[2/7] Temporal split (chronological, 70/15/15)")
    n = len(df)
    train_end, val_end = int(n * 0.70), int(n * 0.85)
    train_df, val_df, test_df = df.iloc[:train_end].copy(), df.iloc[train_end:val_end].copy(), df.iloc[val_end:].copy()

    # Attack-family holdout: these families are removed from TRAIN only.
    train_fit_df = train_df[train_df["is_holdout"] == 0].copy()
    removed = len(train_df) - len(train_fit_df)

    X_train, y_train = train_fit_df[ALL_FEATURE_NAMES].values, train_fit_df["is_fraud"].values
    X_val, y_val = val_df[ALL_FEATURE_NAMES].values, val_df["is_fraud"].values
    X_test, y_test = test_df[ALL_FEATURE_NAMES].values, test_df["is_fraud"].values
    test_amounts = test_df["amount"].values

    periods = {
        "train": {"start": train_df["timestamp"].iloc[0], "end": train_df["timestamp"].iloc[-1],
                  "n": int(len(train_fit_df)), "prevalence": round(float(y_train.mean()), 4)},
        "validation": {"start": val_df["timestamp"].iloc[0], "end": val_df["timestamp"].iloc[-1],
                       "n": int(len(val_df)), "prevalence": round(float(y_val.mean()), 4)},
        "test": {"start": test_df["timestamp"].iloc[0], "end": test_df["timestamp"].iloc[-1],
                 "n": int(len(test_df)), "prevalence": round(float(y_test.mean()), 4)},
    }
    for name, p in periods.items():
        print(f"  {name:<11} n={p['n']:<6} prevalence={p['prevalence']:<7} {p['start'][:19]} -> {p['end'][:19]}")
    print(f"  attack-family holdout: {holdouts}  ({removed} rows removed from TRAIN, still present in TEST)")

    # ------------------------------------------------------------- training
    print(f"\n[3/7] Training {info['architecture']}")
    spw = compute_scale_pos_weight(y_train)
    model = build_classifier(seed=seed, scale_pos_weight=spw)
    model.fit(X_train, y_train)
    print(f"  fitted on {len(X_train)} rows, scale_pos_weight={spw:.2f}")

    # ---------------------------------------------------------- calibration
    print("\n[4/7] Calibrating probabilities on the temporal validation slice")
    calibrator = ProbabilityCalibrator(model, method="isotonic").fit(X_val, y_val)
    raw_test = ProbabilityCalibrator._raw_scores(model, X_test)
    test_probs = calibrator.predict_proba_positive(X_test)
    cal_summary = calibration_summary(y_test, raw_test, test_probs)
    print(f"  ECE before={cal_summary['ece_before_calibration']}  after={cal_summary['ece_after_calibration']}")

    # ----------------------------------------------------------- evaluation
    print("\n[5/7] Evaluating on the held-out temporal test slice")
    overall = evaluate_scores(y_test, test_probs, test_amounts)

    # Attack-family holdout evaluation: unseen families only, vs. legit traffic.
    holdout_eval: Dict[str, Any] = {
        "families_held_out_during_training": holdouts,
        "note": "These attack families were removed from the training slice. Scores below are on unseen families.",
    }
    per_family: Dict[str, Any] = {}
    for fam in holdouts:
        mask = (test_df["attack_family"] == fam).values
        legit = (test_df["is_fraud"] == 0).values
        sel = mask | legit
        if mask.sum() == 0:
            per_family[fam] = {"status": "NOT PRESENT IN TEST SLICE"}
            continue
        per_family[fam] = {
            "n_attack_rows": int(mask.sum()),
            "pr_auc": round(float(average_precision_score(y_test[sel], test_probs[sel])), 4),
            "roc_auc": round(float(roc_auc_score(y_test[sel], test_probs[sel])), 4),
            "recall_at_threshold_0.5": round(float((test_probs[mask] >= 0.5).mean()), 4),
            "mean_score": round(float(test_probs[mask].mean()), 4),
        }
    holdout_eval["per_family"] = per_family

    print(f"  PR-AUC={overall['pr_auc']}  ROC-AUC={overall['roc_auc']}  F1={overall['f1_score']}")
    print(f"  recall@0.5%FPR={overall['recall_at_0.5pct_fpr']}  P@100={overall['precision_at_100']}  FPR={overall['false_positive_rate']}")
    print(f"  net value saved: INR {overall['financial_impact']['net_value_saved_inr']:,.2f}")
    for fam, res in per_family.items():
        if "pr_auc" in res:
            print(f"  holdout family {fam}: PR-AUC={res['pr_auc']} recall={res['recall_at_threshold_0.5']} (n={res['n_attack_rows']})")

    # ------------------------------------------------------- explainability
    print("\n[6/7] Computing SHAP attributions")
    explainer = FeatureExplainer(model, ALL_FEATURE_NAMES)
    shap_global = explainer.global_shap_importance(X_test)
    if shap_global.get("available"):
        top = shap_global["ranked"][:5]
        print(f"  method={shap_global['method']}  top: " + ", ".join(f"{d['feature']}={d['mean_abs_shap']:.4f}" for d in top))
    else:
        print(f"  SHAP unavailable ({shap_global.get('reason')}); falling back to {explainer.method}")

    # ------------------------------------------------------------ artifacts
    print("\n[7/7] Writing artifacts")
    from ..paths import ARTIFACTS_DIR, EVALUATION_DIR, MODELS_DIR

    base_dir = output_dir or ARTIFACTS_DIR
    model_dir = MODELS_DIR if output_dir is None else os.path.join(base_dir, "models")
    eval_dir = EVALUATION_DIR if output_dir is None else os.path.join(base_dir, "evaluation")
    os.makedirs(model_dir, exist_ok=True)
    os.makedirs(eval_dir, exist_ok=True)

    DTLFeatureExtractor.save_schema(os.path.join(model_dir, "feature_schema.json"))
    joblib.dump({"calibrator": calibrator, "model": model, "features": ALL_FEATURE_NAMES},
                os.path.join(model_dir, "forseti_model.joblib"))
    if info["backend"] == "xgboost":
        model.save_model(os.path.join(model_dir, "forseti_xgb.json"))

    metrics = {
        "experiment_id": f"EXP-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "model": info,
        "environment": environment_metadata(seed),
        "dataset": {
            "n_total": int(len(df)),
            "fraud_prevalence": round(float(df["is_fraud"].mean()), 4),
            "feature_count": len(ALL_FEATURE_NAMES),
            "attack_family_counts": {str(k): int(v) for k, v in family_counts.items()},
        },
        "split_periods": periods,
        "attack_family_holdout": holdout_eval,
        "calibration": cal_summary,
        "test_metrics": overall,
        "shap_global": shap_global,
        "explainability_method": explainer.method,
        "elapsed_seconds": round(time.time() - started, 2),
    }
    with open(os.path.join(eval_dir, "metrics.json"), "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2)

    _generate_plots(y_test, test_probs, eval_dir, explainer, X_test)
    print(f"  model   -> {os.path.join(model_dir, 'forseti_model.joblib')}")
    print(f"  metrics -> {os.path.join(eval_dir, 'metrics.json')}")
    print(f"\nCompleted in {metrics['elapsed_seconds']}s")
    print("=" * 74)
    return metrics


def _generate_plots(y_test, y_probs, eval_dir: str, explainer=None, X_test=None) -> None:
    """Renders PR / ROC / calibration / confusion-matrix / SHAP figures."""
    try:
        precision_vals, recall_vals, _ = precision_recall_curve(y_test, y_probs)
        plt.figure(figsize=(6, 4))
        plt.plot(recall_vals, precision_vals, color="#2563eb", lw=2,
                 label=f"PR-AUC = {average_precision_score(y_test, y_probs):.4f}")
        plt.xlabel("Recall"); plt.ylabel("Precision")
        plt.title("Precision-Recall (temporal test slice)")
        plt.legend(loc="lower left"); plt.grid(True, linestyle="--", alpha=0.4)
        plt.tight_layout(); plt.savefig(os.path.join(eval_dir, "pr_curve.png"), dpi=140); plt.close()

        fpr_vals, tpr_vals, _ = roc_curve(y_test, y_probs)
        plt.figure(figsize=(6, 4))
        plt.plot(fpr_vals, tpr_vals, color="#f43f5e", lw=2, label=f"ROC-AUC = {roc_auc_score(y_test, y_probs):.4f}")
        plt.plot([0, 1], [0, 1], color="#94a3b8", linestyle="--")
        plt.xlabel("False positive rate"); plt.ylabel("True positive rate"); plt.title("ROC curve")
        plt.legend(loc="lower right"); plt.grid(True, linestyle="--", alpha=0.4)
        plt.tight_layout(); plt.savefig(os.path.join(eval_dir, "roc_curve.png"), dpi=140); plt.close()

        from sklearn.calibration import calibration_curve

        prob_true, prob_pred = calibration_curve(y_test, y_probs, n_bins=10)
        plt.figure(figsize=(6, 4))
        plt.plot(prob_pred, prob_true, marker="o", color="#10b981", lw=2, label="Calibrated model")
        plt.plot([0, 1], [0, 1], color="#94a3b8", linestyle="--", label="Perfect")
        plt.xlabel("Mean predicted probability"); plt.ylabel("Fraction of positives")
        plt.title("Reliability curve"); plt.legend(loc="upper left"); plt.grid(True, linestyle="--", alpha=0.4)
        plt.tight_layout(); plt.savefig(os.path.join(eval_dir, "calibration.png"), dpi=140); plt.close()

        cm = confusion_matrix(y_test, (np.asarray(y_probs) >= 0.5).astype(int), labels=[0, 1])
        fig, ax = plt.subplots(figsize=(4.5, 4))
        ax.imshow(cm, cmap="Blues")
        for (i, j), v in np.ndenumerate(cm):
            ax.text(j, i, str(v), ha="center", va="center",
                    color="white" if v > cm.max() / 2 else "#0f172a", fontweight="bold")
        ax.set_xticks([0, 1], ["pred legit", "pred fraud"])
        ax.set_yticks([0, 1], ["true legit", "true fraud"])
        ax.set_title("Confusion matrix @ 0.5")
        plt.tight_layout(); plt.savefig(os.path.join(eval_dir, "confusion_matrix.png"), dpi=140); plt.close()
    except Exception as exc:
        print(f"  warning: metric plots failed: {exc}")

    # SHAP summary is only drawn when genuine SHAP values are available.
    try:
        if explainer is not None and explainer.is_genuine_shap and X_test is not None:
            import shap

            sample = np.asarray(X_test, dtype=float)[:400]
            values = explainer._shap_matrix(sample)
            plt.figure()
            shap.summary_plot(values, sample, feature_names=explainer.feature_names, show=False, max_display=15)
            plt.tight_layout(); plt.savefig(os.path.join(eval_dir, "shap_summary.png"), dpi=140); plt.close()
    except Exception as exc:
        print(f"  warning: SHAP summary plot failed: {exc}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Train the FORSETI hybrid detector")
    parser.add_argument("--seed", type=int, default=int(os.environ.get("SEED", 42)))
    parser.add_argument("--samples", type=int, default=12000)
    parser.add_argument("--fraud-ratio", type=float, default=0.012)
    parser.add_argument("--holdout", type=str, default=",".join(DEFAULT_HOLDOUT_FAMILIES),
                        help="Comma-separated attack families withheld from training")
    args = parser.parse_args()
    families = [f.strip() for f in args.holdout.split(",") if f.strip()]
    train_pipeline(seed=args.seed, num_samples=args.samples, fraud_ratio=args.fraud_ratio, holdout_families=families)
