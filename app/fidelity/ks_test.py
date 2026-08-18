from typing import List, Dict, Any
import numpy as np
from scipy import stats
from .canonical_schema import CanonicalTransaction

class KolmogorovSmirnovTest:
    """
    Computes real 2-sample Kolmogorov-Smirnov test on continuous features (transaction amounts).
    """
    @staticmethod
    def calculate_ks(synthetic_txs: List[CanonicalTransaction], anchor_txs: List[CanonicalTransaction]) -> Dict[str, Any]:
        syn_amounts = np.array([tx.amount for tx in synthetic_txs if tx.amount is not None and tx.amount > 0])
        anc_amounts = np.array([tx.amount for tx in anchor_txs if tx.amount is not None and tx.amount > 0])

        if len(syn_amounts) == 0 or len(anc_amounts) == 0:
            return {
                "status": "NOT RUN (Empty sample array)",
                "statistic": None,
                "p_value": None,
                "synthetic_sample_size": len(syn_amounts),
                "anchor_sample_size": len(anc_amounts)
            }

        # Normalize log distributions for cross-currency scale alignment
        log_syn = np.log1p(syn_amounts)
        log_anc = np.log1p(anc_amounts)
        
        # Standardize
        norm_syn = (log_syn - np.mean(log_syn)) / max(1e-4, np.std(log_syn))
        norm_anc = (log_anc - np.mean(log_anc)) / max(1e-4, np.std(log_anc))

        ks_res = stats.ks_2samp(norm_syn, norm_anc)

        return {
            "status": "COMPUTED",
            "statistic": round(float(ks_res.statistic), 4),
            "p_value": round(float(ks_res.pvalue), 4),
            "synthetic_sample_size": len(syn_amounts),
            "anchor_sample_size": len(anc_amounts),
            "interpretation": "High distributional alignment" if ks_res.statistic < 0.08 else "Moderate divergence"
        }
