from typing import List, Dict, Any
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score
from .canonical_schema import CanonicalTransaction

class SyntheticDiscriminator:
    """
    Trains an actual binary classifier to distinguish between Real and Synthetic samples:
    Label 0 = Real Anchor
    Label 1 = Synthetic
    Target ROC-AUC ≈ 0.50 (indistinguishable distributions).
    """
    @staticmethod
    def evaluate_discriminator(
        synthetic_txs: List[CanonicalTransaction],
        anchor_txs: List[CanonicalTransaction],
        seed: int = 42
    ) -> Dict[str, Any]:
        syn_features = [[np.log1p(max(1.0, tx.amount)), float(tx.fraud_label)] for tx in synthetic_txs if tx.amount is not None]
        anc_features = [[np.log1p(max(1.0, tx.amount)), float(tx.fraud_label)] for tx in anchor_txs if tx.amount is not None]

        min_len = min(len(syn_features), len(anc_features))
        if min_len < 50:
            return {
                "status": "NOT RUN (Insufficient anchor samples)",
                "discriminator_auc": None
            }

        syn_sub = syn_features[:min_len]
        anc_sub = anc_features[:min_len]

        X = np.array(anc_sub + syn_sub)
        y = np.array([0] * min_len + [1] * min_len)

        # Shuffle
        np.random.seed(seed)
        perm = np.random.permutation(len(X))
        X_shuffled, y_shuffled = X[perm], y[perm]

        split = int(len(X) * 0.7)
        X_train, X_test = X_shuffled[:split], X_shuffled[split:]
        y_train, y_test = y_shuffled[:split], y_shuffled[split:]

        clf = HistGradientBoostingClassifier(max_iter=50, random_state=seed)
        clf.fit(X_train, y_train)

        probs = clf.predict_proba(X_test)[:, 1]
        auc = float(roc_auc_score(y_test, probs))

        return {
            "status": "COMPUTED",
            "discriminator_auc": round(auc, 4),
            "sample_size_evaluated": len(X),
            "interpretation": "High distributional alignment (near 0.50 random guess)" if abs(auc - 0.50) < 0.15 else "Distinguishable features detected"
        }
