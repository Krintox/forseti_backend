import os
import json
from datetime import datetime, timezone
from typing import Dict, Any, Optional

from .loader import FidelityDatasetLoader
from .ks_test import KolmogorovSmirnovTest
from .categorical_test import CategoricalFidelityTest
from .correlation import CorrelationDistanceTest
from .discriminator import SyntheticDiscriminator
from .tstr import TSTRValidator

def generate_fidelity_report(
    seed: int = 42,
    num_synthetic: int = 5000,
    output_path: Optional[str] = None
) -> Dict[str, Any]:
    print("=" * 70)
    print(f"RUNNING STATISTICAL FIDELITY VALIDATION HARNESS (SEED={seed})")
    print("=" * 70)

    loader = FidelityDatasetLoader()
    avail = loader.check_availability()
    print(f"Anchor dataset directory: {avail['dataset_dir']}")
    print(f"  PaySim CSV available: {avail['paysim_available']}")
    print(f"  ULB CSV available:    {avail['ulb_available']}")

    # Load synthetic dataset
    print(f"\n[1/3] Loading canonical synthetic trajectory ({num_synthetic} samples)...")
    synthetic_txs = loader.load_synthetic(num_samples=num_synthetic, seed=seed)

    # 1. PaySim Evaluation
    print("\n[2/3] Evaluating statistical fidelity against PaySim anchor...")
    paysim_txs, paysim_status = loader.load_paysim(max_samples=5000)
    
    paysim_report = {
        "dataset_name": "PaySim (Mobile Money Anchor, Lopez-Rojas et al.)",
        "dataset_status": paysim_status,
        "metrics": {}
    }
    
    if paysim_txs is not None:
        ks_res = KolmogorovSmirnovTest.calculate_ks(synthetic_txs, paysim_txs)
        js_res = CategoricalFidelityTest.calculate_js_divergence(synthetic_txs, paysim_txs)
        corr_res = CorrelationDistanceTest.calculate_correlation_distance(synthetic_txs, paysim_txs)
        disc_res = SyntheticDiscriminator.evaluate_discriminator(synthetic_txs, paysim_txs, seed=seed)
        tstr_res = TSTRValidator.evaluate_tstr(synthetic_txs, paysim_txs, seed=seed)

        paysim_report["metrics"] = {
            "kolmogorov_smirnov": ks_res,
            "categorical_divergence": js_res,
            "correlation_distance": corr_res,
            "discriminator_auc": disc_res,
            "tstr_transferability": tstr_res
        }
        print(f"  PaySim KS Statistic:     {ks_res.get('statistic')}")
        print(f"  PaySim Discriminator AUC:{disc_res.get('discriminator_auc')}")
    else:
        paysim_report["metrics"] = {
            "status": "NOT RUN / DATASET UNAVAILABLE",
            "reason": "Download paysim.csv to data/anchors/paysim.csv to compute empirical anchor metrics."
        }
        print(f"  PaySim: {paysim_status}")

    # 2. ULB Evaluation
    print("\n[3/3] Evaluating statistical fidelity against ULB Credit Card anchor...")
    ulb_txs, ulb_status = loader.load_ulb(max_samples=5000)
    
    ulb_report = {
        "dataset_name": "ULB creditcard.csv (Card Rail Anchor, Dal Pozzolo et al.)",
        "dataset_status": ulb_status,
        "metrics": {}
    }
    
    if ulb_txs is not None:
        ks_ulb = KolmogorovSmirnovTest.calculate_ks(synthetic_txs, ulb_txs)
        corr_ulb = CorrelationDistanceTest.calculate_correlation_distance(synthetic_txs, ulb_txs)
        disc_ulb = SyntheticDiscriminator.evaluate_discriminator(synthetic_txs, ulb_txs, seed=seed)
        tstr_ulb = TSTRValidator.evaluate_tstr(synthetic_txs, ulb_txs, seed=seed)

        ulb_report["metrics"] = {
            "kolmogorov_smirnov": ks_ulb,
            "correlation_distance": corr_ulb,
            "discriminator_auc": disc_ulb,
            "tstr_transferability": tstr_ulb
        }
        print(f"  ULB KS Statistic:     {ks_ulb.get('statistic')}")
        print(f"  ULB Discriminator AUC:{disc_ulb.get('discriminator_auc')}")
    else:
        ulb_report["metrics"] = {
            "status": "NOT RUN / DATASET UNAVAILABLE",
            "reason": "Download creditcard.csv to data/anchors/creditcard.csv to compute empirical anchor metrics."
        }
        print(f"  ULB: {ulb_status}")

    # Synthetic Internal Calibration
    # Evaluate split 1 vs split 2 of synthetic data to guarantee self-consistency
    mid = len(synthetic_txs) // 2
    self_ks = KolmogorovSmirnovTest.calculate_ks(synthetic_txs[:mid], synthetic_txs[mid:])
    self_disc = SyntheticDiscriminator.evaluate_discriminator(synthetic_txs[:mid], synthetic_txs[mid:], seed=seed)

    anchors_loaded = [
        name for name, rep in (("paysim", paysim_report), ("ulb", ulb_report))
        if rep["dataset_status"] == "LOADED"
    ]
    # The calibration claim is only made when an anchor actually loaded. With no
    # anchor present the only defensible statement is that the comparison did
    # not run - a self-consistency check against our own synthetic data proves
    # the pipeline executes, NOT that the data is realistic.
    if anchors_loaded:
        claim = ("Synthetic generator compared against publicly available real-world transaction "
                 "distributions. Anchors evaluated: " + ", ".join(anchors_loaded) + ".")
        overall_status = "RUN"
    else:
        claim = ("NOT RUN / DATASET UNAVAILABLE. No real-world anchor dataset was present, so NO "
                 "realism or calibration claim is made. The self-consistency figures below compare "
                 "synthetic against synthetic and only demonstrate that the statistical pipeline "
                 "executes end to end.")
        overall_status = "NOT RUN / DATASET UNAVAILABLE"

    full_report = {
        "metadata": {
            "experiment_id": f"FIDELITY-EXP-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "seed": seed,
            "synthetic_samples_generated": len(synthetic_txs),
            "anchor_datasets_loaded": anchors_loaded,
            "overall_status": overall_status,
            "claim_statement": claim,
            "how_to_enable": ("Place paysim.csv and/or creditcard.csv in data/anchors/ "
                              "(see scripts/download_anchors.py) and re-run."),
        },
        "synthetic_internal_consistency": {
            "what_this_measures": "Synthetic split A vs synthetic split B. A pipeline self-test only. "
                                  "It is NOT evidence of real-world realism.",
            "internal_ks_statistic": self_ks.get("statistic"),
            "internal_discriminator_auc": self_disc.get("discriminator_auc")
        },
        "anchors": {
            "paysim": paysim_report,
            "ulb": ulb_report
        }
    }

    if output_path is None:
        from ..paths import FIDELITY_REPORT_PATH

        output_path = FIDELITY_REPORT_PATH
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(full_report, f, indent=2)

    # HTML Report Generation
    html_path = output_path.replace(".json", ".html")
    _generate_html_report(full_report, html_path)

    print(f"\nFidelity report generated: {output_path}")
    print("=" * 70)
    return full_report

def _generate_html_report(report: Dict[str, Any], html_path: str):
    try:
        html = f"""<!DOCTYPE html>
<html>
<head>
    <title>FORSETI Statistical Fidelity Validation Report</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #f8fafc; color: #0f172a; padding: 24px; }}
        .card {{ background: white; border: 1px solid #e2e8f0; border-radius: 12px; padding: 20px; margin-bottom: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }}
        h1, h2, h3 {{ color: #0f172a; }}
        .stat {{ font-family: monospace; font-size: 1.25rem; font-weight: bold; color: #2563eb; }}
        .badge {{ background: #eff6ff; color: #1d4ed8; padding: 4px 8px; border-radius: 6px; font-size: 0.75rem; font-weight: bold; }}
    </style>
</head>
<body>
    <h1>FORSETI Statistical Fidelity & Realism Validation Report</h1>
    <p>Experiment ID: <code>{report['metadata']['experiment_id']}</code> | Generated: {report['metadata']['timestamp']}</p>
    <p><em>{report['metadata']['claim_statement']}</em></p>
    
    <div class="card">
        <h2>Synthetic Self-Consistency Validation</h2>
        <p>Internal KS Statistic: <span class="stat">{report['synthetic_internal_consistency']['internal_ks_statistic']}</span></p>
        <p>Internal Discriminator AUC: <span class="stat">{report['synthetic_internal_consistency']['internal_discriminator_auc']}</span></p>
    </div>

    <div class="card">
        <h2>PaySim Anchor (Lopez-Rojas et al.)</h2>
        <p>Status: <span class="badge">{report['anchors']['paysim']['dataset_status']}</span></p>
        <pre>{json.dumps(report['anchors']['paysim']['metrics'], indent=2)}</pre>
    </div>

    <div class="card">
        <h2>ULB Credit Card Anchor (Dal Pozzolo et al.)</h2>
        <p>Status: <span class="badge">{report['anchors']['ulb']['dataset_status']}</span></p>
        <pre>{json.dumps(report['anchors']['ulb']['metrics'], indent=2)}</pre>
    </div>
</body>
</html>"""
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html)
    except Exception as e:
        print(f"Warning: could not write HTML fidelity report: {e}")

if __name__ == "__main__":
    generate_fidelity_report()
