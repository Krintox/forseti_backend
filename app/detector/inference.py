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

from ..graph_sentinel import PaymentGraph
from ..models.state import DTLGlobalAuthorityState
from ..models.transactions import SyntheticTransaction
from ..paths import MODEL_PATH
from .explainability import FeatureExplainer
from .feature_schema import ALL_FEATURE_NAMES, DTLFeatureExtractor

# Per-authority history depth kept at serving time. Matches the training
# generator's own cap (dataset_builder trims at 60) so the windowed features -
# tx_velocity_1h, cross_rail_velocity, mcc_entropy, amount_deviation_* - see
# the same amount of context they were fitted on.
_SERVING_HISTORY_DEPTH = 60


#: Above this, the model's confidence is doing almost no independent work.
#:
#: `exposure_after_tx_ratio >= 1.0` means the transaction takes cumulative
#: exposure past the delegated ceiling - which is, definitionally, what
#: INV_01_GLOBAL_BUDGET_EXCEEDED checks. In the training data that region is
#: labelled fraud with near-certainty, so an isotonic-calibrated model returns
#: values at or near 1.0 there.
#:
#: That is not a bug and not a leak: the feature is a legitimate aggregate the
#: model is entitled to read. But a calibrated 1.0000 on screen invites "your
#: model is overconfident", and the honest answer is narrower and more useful:
#: in this region the classifier is agreeing with arithmetic the invariant has
#: already done, and is adding little. Its independent value is in the region
#: BELOW the ceiling, where behaviour is the only signal.
_DETERMINISTIC_REGION_RATIO = 1.0


def confidence_provenance(probability: float, features: Dict[str, float]) -> Dict[str, Any]:
    """
    Says WHY a probability is what it is, when the answer is "arithmetic".

    Surfaced next to the score so a near-1.0 reading is read correctly rather
    than as the model being sure of something subtle.
    """
    ratio = float(features.get("exposure_after_tx_ratio", 0.0) or 0.0)
    in_deterministic_region = ratio >= _DETERMINISTIC_REGION_RATIO
    return {
        "exposure_after_tx_ratio": round(ratio, 4),
        "in_deterministic_region": in_deterministic_region,
        "note": (
            "This transaction takes exposure past the delegated ceiling. That condition is "
            "definitionally a breach, the training labels reflect it, and a calibrated model "
            "therefore saturates near 1.0 here - it is agreeing with arithmetic INV_01 has "
            "already done, not adding independent evidence. The model's own contribution is "
            "below the ceiling, where behaviour is the only signal."
            if in_deterministic_region else
            "Exposure stays inside the delegated ceiling, so no invariant settles this case. "
            "The score here is the model's own judgement on behavioural shape."
        ),
    }


class HybridMLDetectorInference:
    """
    Loads the trained GBDT + calibrator and scores single transactions.

    SERVING CONTEXT (added to close a measured train/serve skew): the extractor
    computes 37 features, but 23 of them are windowed (needing per-authority
    transaction history) or cross-authority (needing an entity graph). The
    previous `score()` called `extract_features(auth, tx)` with neither, so
    those 23 collapsed to exactly 0.0 at serving while training populated all
    of them - including `graph_merchant_pagerank`, which was the model's #2
    SHAP driver and was pinned to zero live. The live "ML RISK %" was therefore
    produced by a model operating far outside its training distribution.

    This class now carries the same two pieces of state the training loop does,
    and observes transactions as they are scored, so the same feature pipeline
    runs in both places. `stateless_score()` remains available for callers that
    genuinely want a context-free score (e.g. the latency benchmark's isolated
    feature-extraction stage).
    """

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
        # --- serving context, mirroring the training loop's own state ---
        self._histories: Dict[str, list] = {}
        self._graph = PaymentGraph()
        self._load_model(eager_explainer)

    # ------------------------------------------------------- serving context

    def reset_context(self) -> None:
        """Clears serving history and the live graph (used on arena reset)."""
        self._histories.clear()
        self._graph = PaymentGraph()

    def observe(self, auth: DTLGlobalAuthorityState, tx: SyntheticTransaction) -> None:
        """
        Records a transaction into the serving context AFTER it has been
        scored. Kept separate from `score()` so the snapshot-before-add
        discipline the training loop uses is preserved exactly: a transaction
        never contributes to the graph or history that produced its own
        features.
        """
        history = self._histories.setdefault(auth.authority_id, [])
        history.append({
            "tx_id": tx.tx_id,
            "rail": str(getattr(tx.rail, "value", tx.rail)),
            "amount": float(tx.amount),
            "mcc": tx.merchant_mcc,
            "merchant_id": tx.merchant_id,
            "timestamp": (tx.created_at.isoformat() if tx.created_at else None),
        })
        if len(history) > _SERVING_HISTORY_DEPTH:
            history.pop(0)
        self._graph.add_transaction(auth.agent_id, tx.merchant_id, tx.device_id)

    def with_fresh_context(self) -> "HybridMLDetectorInference":
        """
        A sibling sharing this instance's loaded model/explainer but with its
        OWN empty serving context.

        Counterfactual ("what if the ceiling had been X?") runs must not write
        into the live context - otherwise a hypothetical would inflate the real
        session's velocity and graph features. Before the serving context
        existed the sandbox could simply share the detector because scoring was
        genuinely stateless; it no longer is, so sharing has to be explicit
        about what is shared (the model) and what is not (the context).
        """
        sibling = object.__new__(HybridMLDetectorInference)
        sibling.model_path = self.model_path
        sibling.model = self.model
        sibling.raw_model = self.raw_model
        sibling.feature_names = list(self.feature_names)
        sibling.explainer = self.explainer
        sibling.backend_name = self.backend_name
        sibling.load_error = self.load_error
        sibling.explain_in_hot_path = self.explain_in_hot_path
        sibling._histories = {}
        sibling._graph = PaymentGraph()
        return sibling

    def refresh_graph_metrics(self) -> None:
        """
        Recomputes the global graph metrics (PageRank, betweenness, Louvain
        communities) for the serving graph.

        Deliberately NOT called per transaction. Measured cost is ~4 ms on a
        session-sized graph against an inline p99 of ~0.9 ms, so refreshing in
        the authorization path would inflate per-transaction latency roughly
        five-fold for a signal that changes slowly. It is instead called at
        round boundaries - which is also how graph analytics are deployed in
        practice: a streaming/batch layer alongside the authorization path
        rather than inside it.

        Without this the four global graph features stay at 0.0 for an entire
        session, because PaymentGraph's own internal cadence (REFRESH_EVERY =
        200 transactions) is sized for a 12,000-row training run and never
        fires in a ~30-transaction arena session. That was a real component of
        the train/serve skew: graph_merchant_pagerank was the model's #2 SHAP
        driver and was pinned to zero live.
        """
        self._graph.refresh_global_metrics()

    def context_status(self) -> Dict[str, Any]:
        """What the live serving context currently holds - surfaced in the UI."""
        return {
            "authorities_tracked": len(self._histories),
            "transactions_in_history": sum(len(h) for h in self._histories.values()),
            "graph_nodes": self._graph.node_count(),
            "graph_edges": self._graph.edge_count(),
            "graph_transactions_ingested": self._graph.transaction_count(),
        }

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
        """
        Returns (calibrated_probability, extracted_features), using the live
        serving context so the feature vector has the same shape it had in
        training. The graph snapshot is taken BEFORE this transaction's own
        edge is added (see `observe`), matching the generator exactly.
        """
        history = self._histories.get(auth.authority_id, [])
        graph_feats = self._graph.snapshot_features(auth.agent_id, tx.merchant_id, tx.device_id)
        features = DTLFeatureExtractor.extract_features(
            auth, tx, history, graph_features=graph_feats
        )
        if not self.model_loaded:
            return float("nan"), features
        vec = np.array([[features.get(n, 0.0) for n in self.feature_names]], dtype=float)
        prob = float(np.asarray(self.model.predict_proba(vec))[0, 1])
        return prob, features

    def stateless_score(
        self, auth: DTLGlobalAuthorityState, tx: SyntheticTransaction
    ) -> Tuple[float, Dict[str, float]]:
        """
        Context-free score, for callers that deliberately want no history or
        graph (the isolated feature-extraction latency stage). Kept explicit
        so that path is a stated choice rather than an accident - which is how
        the original skew arose.
        """
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
        # Ships with EVERY evaluation, not only explained ones: a saturated
        # score is exactly the case someone screenshots.
        explanation["confidence_provenance"] = confidence_provenance(prob, features)
        return prob, is_anomaly, explanation
