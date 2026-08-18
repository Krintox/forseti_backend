import os
from typing import List, Optional, Tuple
import pandas as pd
import numpy as np
from datetime import datetime

from .canonical_schema import CanonicalTransaction
from ..detector.dataset_builder import SyntheticMLDatasetBuilder

class FidelityDatasetLoader:
    """
    Manages loading and mapping of real anchor datasets (PaySim, ULB) and synthetic datasets.
    Explicitly tracks dataset availability on disk. Never invents missing data.
    """
    def __init__(self, dataset_dir: Optional[str] = None):
        from ..paths import DATASET_DIR

        self.dataset_dir = dataset_dir or DATASET_DIR
        os.makedirs(self.dataset_dir, exist_ok=True)

    def check_availability(self) -> dict:
        paysim_path = os.path.join(self.dataset_dir, "paysim.csv")
        ulb_path = os.path.join(self.dataset_dir, "creditcard.csv")
        return {
            "paysim_available": os.path.exists(paysim_path),
            "paysim_path": paysim_path if os.path.exists(paysim_path) else None,
            "ulb_available": os.path.exists(ulb_path),
            "ulb_path": ulb_path if os.path.exists(ulb_path) else None,
            "dataset_dir": self.dataset_dir
        }

    def load_synthetic(self, num_samples: int = 5000, seed: int = 42) -> List[CanonicalTransaction]:
        """
        Loads canonical representation of synthetic payment dataset.
        """
        builder = SyntheticMLDatasetBuilder(seed=seed)
        df = builder.generate_trajectory(num_samples=num_samples, fraud_ratio=0.05)
        
        canonical_list = []
        for idx, row in df.iterrows():
            tx = CanonicalTransaction(
                transaction_id=row["tx_id"],
                timestamp=datetime.fromisoformat(row["timestamp"]) if "timestamp" in row else None,
                amount=float(row["amount"]),
                currency="INR",
                merchant_category=str(int(row["merchant_mcc_code"])),
                rail="UPI" if row["rail_code"] == 1.0 else ("AGENTIC" if row["rail_code"] == 2.0 else "CARD"),
                fraud_label=int(row["is_fraud"]),
                dataset_source="FORSETI_SYNTHETIC"
            )
            canonical_list.append(tx)
        return canonical_list

    def load_paysim(self, max_samples: int = 10000) -> Tuple[Optional[List[CanonicalTransaction]], str]:
        """
        Loads PaySim dataset if present in anchor directory.
        """
        avail = self.check_availability()
        if not avail["paysim_available"]:
            return None, "PaySim CSV not found in data/anchors/paysim.csv"

        try:
            df = pd.read_csv(avail["paysim_path"], nrows=max_samples)
            canonical = []
            for idx, row in df.iterrows():
                tx = CanonicalTransaction(
                    transaction_id=f"paysim_{idx}",
                    amount=float(row.get("amount", 0.0)),
                    currency="USD",
                    merchant_category=str(row.get("type", "PAYMENT")),
                    rail="MOBILE_MONEY",
                    customer_id=str(row.get("nameOrig", None)),
                    fraud_label=int(row.get("isFraud", 0)),
                    dataset_source="PAYSIM_ANCHOR"
                )
                canonical.append(tx)
            return canonical, "LOADED"
        except Exception as e:
            return None, f"Error reading PaySim CSV: {e}"

    def load_ulb(self, max_samples: int = 10000) -> Tuple[Optional[List[CanonicalTransaction]], str]:
        """
        Loads ULB creditcard.csv dataset if present in anchor directory.
        """
        avail = self.check_availability()
        if not avail["ulb_available"]:
            return None, "ULB creditcard.csv not found in data/anchors/creditcard.csv"

        try:
            df = pd.read_csv(avail["ulb_path"], nrows=max_samples)
            canonical = []
            for idx, row in df.iterrows():
                tx = CanonicalTransaction(
                    transaction_id=f"ulb_{idx}",
                    amount=float(row.get("Amount", 0.0)),
                    currency="EUR",
                    merchant_category=None, # Not present in ULB PCA dataset
                    rail="CARD",
                    fraud_label=int(row.get("Class", 0)),
                    dataset_source="ULB_ANCHOR"
                )
                canonical.append(tx)
            return canonical, "LOADED"
        except Exception as e:
            return None, f"Error reading ULB CSV: {e}"
