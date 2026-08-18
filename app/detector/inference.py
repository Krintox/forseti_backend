"""
Production inference path for the FORSETI hybrid detector.

Loads the trained artifact from disk. It never trains at import or API startup.
If no artifact exists the detector reports model_loaded=False and callers must
surface "MODEL NOT TRAINED" rather than presenting heuristic output as a model
score.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional, Tuple

import joblib
import numpy as np

from ..models.state import DTLGlobalAuthorityState
from ..models.transactions import SyntheticTransaction
from ..paths import MODEL_PATH
from .explainability import FeatureExplainer
from .feature_schema import ALL_FEATURE_NAMES, DTLFeatureExtractor


class HybridMLDetectorInference:
    """Loads the trained GBDT + calibrator and scores single transactions."""

    def __init__(self, model_path: Optional[str] = None, eager_explainer: bool = True):
        self.model_path = model_path or MODEL_PATH
        self.model: Optional[Any] = None          # calibrated scorer used for probabilities
        self.raw_model: Optional[Any] = None      # underlying tree ensemble (for SHAP)
        self.feature_names = list(ALL_FEATURE_NAMES)
        self.explainer: Optional[FeatureExplainer] = None
        self.backend_name = "none"
        self.load_error: Optional[str] = None
        # Explanations are NOT part of the inline hot path; they are computed on
        # request so latency numbers reflect what a real authorizer would run.
        self.explain_in_hot_path = False
        self._load_model(eager_explainer)

    def _load_model(self, eager_explainer: bool) -> None:
        if not os.path.exists(self.model_path):
            self.load_error = f"No model artifact at {self.model_path}. Run `python -m app.detector.train`."
            return
        try:
            blob = joblib.load(self.model_path)
            if isinstance(blob, dict):
                self.model = blob.get("calibrator") or blob.get("model")
                self.raw_model = blob.get("model", self.model)
                self.feature_names = blob.get("features", list(ALL_FEATURE_NAMES))
            else:
                self.model = blob
                self.raw_model = getattr(blob, "base_estimator", blob)
            self.backend_name = type(self.raw_model).__name__
            if eager_explainer:
                self.explainer = FeatureExplainer(self.raw_model, self.feature_names)
        except Exception as exc:
            self.load_error = f"{type(exc).__name__}: {exc}"
            self.model = None

    @property
    def model_loaded(self) -> bool:
        return self.model is not None

    def status(self) -> Dict[str, Any]:
        return {
            "model_loaded": self.model_loaded,
            "model_path": self.model_path,
            "backend": self.backend_name,
            "feature_count": len(self.feature_names),
            "load_error": self.load_error,
            "explainability_method": self.explainer.method if self.explainer else None,
            "is_genuine_shap": bool(self.explainer and self.explainer.is_genuine_shap),
        }

    def score(self, auth: DTLGlobalAuthorityState, tx: SyntheticTransaction) -> Tuple[float, Dict[str, float]]:
        """Returns (calibrated_probability, extracted_features)."""
        features = DTLFeatureExtractor.extract_features(auth, tx)
        if not self.model_loaded:
            return float("nan"), features
        vec = np.array([[features.get(n, 0.0) for n in self.feature_names]], dtype=float)
        prob = float(np.asarray(self.model.predict_proba(vec))[0, 1])
        return prob, features

    def explain(self, features: Dict[str, float]) -> Dict[str, Any]:
        """Out-of-band explanation for one transaction."""
        if self.explainer is None:
            if self.raw_model is None:
                return {"method": "unavailable", "is_genuine_shap": False,
                        "reason": self.load_error or "model not loaded", "contributions": {}}
            self.explainer = FeatureExplainer(self.raw_model, self.feature_names)
        return self.explainer.explain_instance(features)

    def evaluate_transaction(
        self,
        auth: DTLGlobalAuthorityState,
        tx: SyntheticTransaction,
        threshold: float = 0.50,
        explain: bool = False,
    ) -> Tuple[float, bool, Dict[str, Any]]:
        """
        Scores a transaction. Returns (probability, is_anomaly, explanation).

        `explain=False` keeps SHAP out of the measured inline path; the arena
        and the explainability API request it explicitly.
        """
        prob, features = self.score(auth, tx)
        if not self.model_loaded:
            return float("nan"), False, {
                "method": "unavailable",
                "is_genuine_shap": False,
                "reason": self.load_error,
                "contributions": {},
            }
        is_anomaly = bool(prob >= threshold)
        explanation: Dict[str, Any] = {"method": "not_requested", "contributions": {}}
        if explain:
            explanation = self.explain(features)
        return prob, is_anomaly, explanation
