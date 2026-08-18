"""
Probability calibration for the FORSETI detector.

The raw GBDT margin is not a calibrated probability. We fit an isotonic or
Platt (sigmoid) calibrator on a held-out *validation* slice that is strictly
later in time than the training slice, so calibration never sees training rows
and never sees the test slice.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import numpy as np


class ProbabilityCalibrator:
    """Wraps a fitted base estimator with a post-hoc probability calibrator."""

    def __init__(self, base_estimator: Any, method: str = "isotonic"):
        self.base_estimator = base_estimator
        self.method = method
        self.calibrator: Optional[Any] = None
        self.fitted = False

    @staticmethod
    def _raw_scores(estimator: Any, X: np.ndarray) -> np.ndarray:
        if hasattr(estimator, "predict_proba"):
            return np.asarray(estimator.predict_proba(X))[:, 1]
        return np.asarray(estimator.decision_function(X))

    def fit(self, X_val: np.ndarray, y_val: np.ndarray) -> "ProbabilityCalibrator":
        """Fits the calibrator on the temporal validation slice."""
        raw = self._raw_scores(self.base_estimator, X_val)
        y_val = np.asarray(y_val)

        # Calibration needs both classes present.
        if len(np.unique(y_val)) < 2:
            self.fitted = False
            return self

        if self.method == "isotonic":
            from sklearn.isotonic import IsotonicRegression

            self.calibrator = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
            self.calibrator.fit(raw, y_val)
        else:
            from sklearn.linear_model import LogisticRegression

            self.calibrator = LogisticRegression(C=1e6, solver="lbfgs")
            self.calibrator.fit(raw.reshape(-1, 1), y_val)

        self.fitted = True
        return self

    def predict_proba_positive(self, X: np.ndarray) -> np.ndarray:
        """Returns calibrated P(fraud) in [0, 1]."""
        raw = self._raw_scores(self.base_estimator, X)
        if not self.fitted or self.calibrator is None:
            return np.clip(raw, 0.0, 1.0)

        if self.method == "isotonic":
            out = self.calibrator.predict(raw)
        else:
            out = self.calibrator.predict_proba(raw.reshape(-1, 1))[:, 1]
        return np.clip(np.asarray(out, dtype=float), 0.0, 1.0)

    # sklearn-compatible surface so downstream code can treat this like a model
    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        p = self.predict_proba_positive(X)
        return np.column_stack([1.0 - p, p])

    def predict(self, X: np.ndarray, threshold: float = 0.5) -> np.ndarray:
        return (self.predict_proba_positive(X) >= threshold).astype(int)


def expected_calibration_error(y_true: np.ndarray, y_prob: np.ndarray, n_bins: int = 10) -> float:
    """
    Expected Calibration Error: mean |accuracy - confidence| weighted by bin
    population. Lower is better; 0.0 is perfect calibration.
    """
    y_true = np.asarray(y_true, dtype=float)
    y_prob = np.asarray(y_prob, dtype=float)
    if len(y_true) == 0:
        return 0.0

    bins = np.linspace(0.0, 1.0, n_bins + 1)
    idx = np.digitize(y_prob, bins[1:-1], right=True)
    ece = 0.0
    for b in range(n_bins):
        mask = idx == b
        if not mask.any():
            continue
        conf = float(y_prob[mask].mean())
        acc = float(y_true[mask].mean())
        ece += (mask.sum() / len(y_true)) * abs(acc - conf)
    return float(ece)


def calibration_summary(y_true: np.ndarray, y_prob_raw: np.ndarray, y_prob_cal: np.ndarray) -> Dict[str, Any]:
    """Reports measured ECE before and after calibration."""
    return {
        "method": "isotonic regression on temporal validation slice",
        "ece_before_calibration": round(expected_calibration_error(y_true, y_prob_raw), 5),
        "ece_after_calibration": round(expected_calibration_error(y_true, y_prob_cal), 5),
        "n_test_samples": int(len(y_true)),
    }
