"""
Model factory for the FORSETI hybrid detector.

Resolution order (per project spec):
  1. XGBoost      (preferred)
  2. LightGBM     (fallback)
  3. scikit-learn HistGradientBoostingClassifier (last resort)

The resolved backend is recorded in every artifact so the UI and README can
state exactly which model produced a metric. There is no manually-weighted
sigmoid anywhere in this path.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple


def resolve_backend() -> Tuple[str, str]:
    """Returns (backend_id, version) for the best available GBDT library."""
    try:
        import xgboost

        return "xgboost", xgboost.__version__
    except Exception:
        pass
    try:
        import lightgbm

        return "lightgbm", lightgbm.__version__
    except Exception:
        pass
    import sklearn

    return "sklearn_hgb", sklearn.__version__


BACKEND, BACKEND_VERSION = resolve_backend()

MODEL_ARCHITECTURE_LABELS = {
    "xgboost": "XGBoost XGBClassifier (gradient-boosted decision trees)",
    "lightgbm": "LightGBM LGBMClassifier (gradient-boosted decision trees)",
    "sklearn_hgb": "scikit-learn HistGradientBoostingClassifier",
}


def build_classifier(seed: int = 42, scale_pos_weight: float = 1.0, n_estimators: int = 300) -> Any:
    """
    Constructs an untrained GBDT classifier using the best available backend.

    `scale_pos_weight` carries the class-imbalance correction; for the sklearn
    fallback this is expressed as class_weight="balanced" instead.
    """
    if BACKEND == "xgboost":
        from xgboost import XGBClassifier

        return XGBClassifier(
            n_estimators=n_estimators,
            max_depth=6,
            learning_rate=0.08,
            subsample=0.9,
            colsample_bytree=0.9,
            min_child_weight=2.0,
            reg_lambda=1.0,
            objective="binary:logistic",
            eval_metric="aucpr",
            tree_method="hist",
            scale_pos_weight=float(scale_pos_weight),
            random_state=seed,
            n_jobs=4,
        )

    if BACKEND == "lightgbm":
        from lightgbm import LGBMClassifier

        return LGBMClassifier(
            n_estimators=n_estimators,
            max_depth=-1,
            num_leaves=31,
            learning_rate=0.08,
            subsample=0.9,
            colsample_bytree=0.9,
            reg_lambda=1.0,
            scale_pos_weight=float(scale_pos_weight),
            random_state=seed,
            n_jobs=4,
            verbose=-1,
        )

    from sklearn.ensemble import HistGradientBoostingClassifier

    return HistGradientBoostingClassifier(
        max_iter=n_estimators,
        learning_rate=0.08,
        max_leaf_nodes=31,
        min_samples_leaf=20,
        random_state=seed,
        class_weight="balanced",
    )


def backend_info() -> Dict[str, Any]:
    return {
        "backend": BACKEND,
        "backend_version": BACKEND_VERSION,
        "architecture": MODEL_ARCHITECTURE_LABELS.get(BACKEND, BACKEND),
        "is_preferred_backend": BACKEND == "xgboost",
    }


def compute_scale_pos_weight(y) -> float:
    """Ratio of negatives to positives, used for imbalance correction."""
    import numpy as np

    y = np.asarray(y)
    pos = float((y == 1).sum())
    neg = float((y == 0).sum())
    if pos <= 0:
        return 1.0
    return max(1.0, neg / pos)
