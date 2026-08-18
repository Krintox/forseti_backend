from typing import List, Dict, Any
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import average_precision_score, roc_auc_score
from .canonical_schema import CanonicalTransaction

class TSTRValidator:
    """
    Train-on-Synthetic, Test-on-Real (TSTR) Framework.
    Compares:
    1. Model trained on Synthetic -> Evaluated on Real Anchor Test Split
    2. Model trained on Real -> Evaluated on Real Anchor Test Split
    Calculates TSTR retention ratio.
    """
    @staticmethod
    def evaluate_tstr(
        synthetic_txs: List[CanonicalTransaction],
        anchor_txs: List[CanonicalTransaction],
        seed: int = 42
    ) -> Dict[str, Any]:
        syn_X = np.array([[np.log1p(max(1.0, tx.amount))] for tx in synthetic_txs if tx.amount is not None])
        syn_y = np.array([tx.fraud_label for tx in synthetic_txs if tx.amount is not None])

        anc_X = np.array([[np.log1p(max(1.0, tx.amount))] for tx in anchor_txs if tx.amount is not None])
        anc_y = np.array([tx.fraud_label for tx in anchor_txs if tx.amount is not None])

        if len(anc_X) < 100 or np.sum(anc_y) == 0:
            return {
                "status": "NOT RUN (Insufficient anchor samples or zero fraud positive class in anchor)",
                "tstr_retention_ratio": None,
                "pr_auc_synthetic_on_real": None,
                "pr_auc_real_on_real": None
            }

        # Split Real into Train (50%) and Test (50%)
        split = int(len(anc_X) * 0.5)
        anc_train_X, anc_test_X = anc_X[:split], anc_X[split:]
        anc_train_y, anc_test_y = anc_y[:split], anc_y[split:]

        # 1. Train on Synthetic, Test on Real
        m_syn = HistGradientBoostingClassifier(max_iter=50, random_state=seed)
        m_syn.fit(syn_X, syn_y)
        probs_syn_on_real = m_syn.predict_proba(anc_test_X)[:, 1]
        pr_syn = float(average_precision_score(anc_test_y, probs_syn_on_real))

        # 2. Train on Real, Test on Real
        m_real = HistGradientBoostingClassifier(max_iter=50, random_state=seed)
        m_real.fit(anc_train_X, anc_train_y)
        probs_real_on_real = m_real.predict_proba(anc_test_X)[:, 1]
        pr_real = float(average_precision_score(anc_test_y, probs_real_on_real))

        retention_ratio = round(pr_syn / max(1e-4, pr_real), 4)

        return {
            "status": "COMPUTED",
            "pr_auc_synthetic_on_real": round(pr_syn, 4),
            "pr_auc_real_on_real": round(pr_real, 4),
            "tstr_retention_ratio": retention_ratio,
            "tstr_transferability_pct": round(min(100.0, retention_ratio * 100), 1)
        }
