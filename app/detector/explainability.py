"""
Explainability for the FORSETI hybrid detector.

HONESTY CONTRACT
----------------
`method` is reported on every result:
  - "shap.TreeExplainer"  -> genuine Shapley values from the `shap` package.
  - "model_feature_contribution" -> a documented fallback that is explicitly
    NOT SHAP and is never labelled as such in the API or the UI.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np

SHAP_METHOD = "shap.TreeExplainer"
FALLBACK_METHOD = "model_feature_contribution"


class FeatureExplainer:
    """Global feature importance plus per-transaction attributions."""

    def __init__(self, model: Any, feature_names: List[str], background: Optional[np.ndarray] = None):
        self.model = model
        self.feature_names = list(feature_names)
        self._tree_model = self._unwrap(model)
        self.explainer = None
        self.method = FALLBACK_METHOD
        self.unavailable_reason: Optional[str] = None
        self._init_shap(background)

    @staticmethod
    def _unwrap(model: Any) -> Any:
        """Digs the raw tree ensemble out of a calibration wrapper."""
        return getattr(model, "base_estimator", model)

    def _init_shap(self, background: Optional[np.ndarray]) -> None:
        try:
            import shap

            self.explainer = shap.TreeExplainer(self._tree_model)
            self.method = SHAP_METHOD
        except Exception as exc:
            self.explainer = None
            self.method = FALLBACK_METHOD
            self.unavailable_reason = f"{type(exc).__name__}: {exc}"

    @property
    def is_genuine_shap(self) -> bool:
        return self.method == SHAP_METHOD and self.explainer is not None

    def get_global_importance(self) -> Dict[str, float]:
        """Normalized global importance from the trained tree ensemble."""
        importances: Dict[str, float] = {}
        raw_imp = getattr(self._tree_model, "feature_importances_", None)
        if raw_imp is not None and len(raw_imp) == len(self.feature_names):
            total = max(1e-9, float(np.sum(raw_imp)))
            for name, val in zip(self.feature_names, raw_imp):
                importances[name] = round(float(val) / total, 5)
        else:
            uniform = 1.0 / max(1, len(self.feature_names))
            for name in self.feature_names:
                importances[name] = round(uniform, 5)
        return dict(sorted(importances.items(), key=lambda kv: kv[1], reverse=True))

    def global_shap_importance(self, X: np.ndarray, max_rows: int = 500) -> Dict[str, Any]:
        """Mean |SHAP value| per feature across a sample of rows."""
        if not self.is_genuine_shap:
            return {
                "method": self.method,
                "available": False,
                "reason": self.unavailable_reason or "SHAP not available",
                "importance": self.get_global_importance(),
            }
        X = np.asarray(X, dtype=float)
        if len(X) > max_rows:
            X = X[:max_rows]
        try:
            values = self._shap_matrix(X)
            mean_abs = np.abs(values).mean(axis=0)
            ranked = sorted(
                ({"feature": n, "mean_abs_shap": round(float(v), 6)} for n, v in zip(self.feature_names, mean_abs)),
                key=lambda d: d["mean_abs_shap"],
                reverse=True,
            )
            return {"method": SHAP_METHOD, "available": True, "rows_explained": int(len(X)), "ranked": ranked}
        except Exception as exc:
            return {
                "method": self.method,
                "available": False,
                "reason": f"{type(exc).__name__}: {exc}",
                "importance": self.get_global_importance(),
            }

    def _shap_matrix(self, X: np.ndarray) -> np.ndarray:
        """Returns a (n_rows, n_features) SHAP matrix for the positive class."""
        values = self.explainer.shap_values(X, check_additivity=False)
        arr = np.asarray(values)
        # Binary classifiers may return a list/3-D array of per-class values.
        if arr.ndim == 3:
            arr = arr[:, :, -1] if arr.shape[-1] == 2 else arr[-1]
        return arr

    def explain_instance(self, feature_dict: Dict[str, float]) -> Dict[str, Any]:
        """
        Per-transaction attribution. Returns a dict carrying the method label so
        callers can never mistake the fallback for real SHAP.
        """
        vector = np.array([[float(feature_dict.get(name, 0.0)) for name in self.feature_names]], dtype=float)

        if self.is_genuine_shap:
            try:
                row = self._shap_matrix(vector)[0]
                contributions = {
                    name: round(float(val), 6)
                    for name, val in zip(self.feature_names, row)
                    if abs(float(val)) > 1e-6
                }
                ranked = dict(sorted(contributions.items(), key=lambda kv: abs(kv[1]), reverse=True))
                base = self.explainer.expected_value
                if isinstance(base, (list, np.ndarray)):
                    base = float(np.asarray(base).ravel()[-1])
                return {
                    "method": SHAP_METHOD,
                    "is_genuine_shap": True,
                    "base_value": round(float(base), 6),
                    "contributions": ranked,
                    "top_features": list(ranked.items())[:8],
                }
            except Exception as exc:
                self.unavailable_reason = f"{type(exc).__name__}: {exc}"

        # Documented non-SHAP fallback: importance-weighted feature magnitude.
        global_imp = self.get_global_importance()
        contributions = {}
        for feat in self.feature_names:
            contrib = float(feature_dict.get(feat, 0.0)) * global_imp.get(feat, 0.0)
            if abs(contrib) > 1e-6:
                contributions[feat] = round(contrib, 6)
        ranked = dict(sorted(contributions.items(), key=lambda kv: abs(kv[1]), reverse=True))
        return {
            "method": FALLBACK_METHOD,
            "is_genuine_shap": False,
            "note": "Model feature contribution (importance-weighted magnitude). This is NOT SHAP.",
            "reason": self.unavailable_reason,
            "contributions": ranked,
            "top_features": list(ranked.items())[:8],
        }
