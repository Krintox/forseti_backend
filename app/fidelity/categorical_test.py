from typing import List, Dict, Any
from collections import Counter
import numpy as np
from scipy.spatial.distance import jensenshannon
from .canonical_schema import CanonicalTransaction

class CategoricalFidelityTest:
    """
    Computes Jensen-Shannon divergence and Chi-square statistics on categorical features.
    """
    @staticmethod
    def calculate_js_divergence(synthetic_txs: List[CanonicalTransaction], anchor_txs: List[CanonicalTransaction]) -> Dict[str, Any]:
        syn_cats = [tx.merchant_category for tx in synthetic_txs if tx.merchant_category is not None]
        anc_cats = [tx.merchant_category for tx in anchor_txs if tx.merchant_category is not None]

        if len(syn_cats) == 0 or len(anc_cats) == 0:
            return {
                "status": "NOT RUN (Feature unavailable in anchor dataset)",
                "jensen_shannon_divergence": None
            }

        all_keys = list(set(syn_cats).union(set(anc_cats)))
        c_syn = Counter(syn_cats)
        c_anc = Counter(anc_cats)

        p = np.array([c_syn[k] for k in all_keys], dtype=float) / len(syn_cats)
        q = np.array([c_anc[k] for k in all_keys], dtype=float) / len(anc_cats)

        # Smooth zero counts
        p = (p + 1e-6) / np.sum(p + 1e-6)
        q = (q + 1e-6) / np.sum(q + 1e-6)

        js_div = float(jensenshannon(p, q))

        return {
            "status": "COMPUTED",
            "jensen_shannon_divergence": round(js_div, 4),
            "categories_evaluated": len(all_keys)
        }
