import os
import json
from typing import Dict, Any
from .report import generate_fidelity_report

class FidelityValidationHarness:
    """
    Statistical Fidelity Harness validating synthetic payment distributions
    against real-world empirical benchmarks (PaySim and ULB datasets).
    """
    @staticmethod
    def compute_fidelity_report() -> Dict[str, Any]:
        from ..paths import FIDELITY_REPORT_PATH

        report_path = FIDELITY_REPORT_PATH
        if os.path.exists(report_path):
            try:
                with open(report_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        # Dynamically generate if report doesn't exist
        return generate_fidelity_report()
