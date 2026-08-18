from typing import List, Dict, Any
import numpy as np
from .canonical_schema import CanonicalTransaction

class CorrelationDistanceTest:
    """
    Computes correlation matrix distance (Frobenius norm of correlation difference)
    between synthetic and anchor distributions.
    """
    @staticmethod
    def calculate_correlation_distance(synthetic_txs: List[CanonicalTransaction], anchor_txs: List[CanonicalTransaction]) -> Dict[str, Any]:
        # Extract common numeric features (amount, fraud_label)
        syn_data = np.array([[tx.amount, float(tx.fraud_label)] for tx in synthetic_txs if tx.amount is not None])
        anc_data = np.array([[tx.amount, float(tx.fraud_label)] for tx in anchor_txs if tx.amount is not None])

        if len(syn_data) < 10 or len(anc_data) < 10:
            return {
                "status": "NOT RUN (Insufficient numeric samples)",
                "correlation_matrix_distance": None
            }

        corr_syn = np.corrcoef(syn_data, rowvar=False)
        corr_anc = np.corrcoef(anc_data, rowvar=False)

        # Handle any nan
        corr_syn = np.nan_to_num(corr_syn, nan=0.0)
        corr_anc = np.nan_to_num(corr_anc, nan=0.0)

        diff = corr_syn - corr_anc
        frobenius_norm = float(np.linalg.norm(diff, ord='fro'))
        normalized_distance = frobenius_norm / (np.sqrt(diff.shape[0] * diff.shape[1]))

        return {
            "status": "COMPUTED",
            "frobenius_distance": round(frobenius_norm, 4),
            "correlation_matrix_distance": round(normalized_distance, 4)
        }
