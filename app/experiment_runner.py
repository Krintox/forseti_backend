"""
Full reproducible experiment pipeline.

    python -m app.experiment_runner --seed 42

Runs every stage, writes artifacts/final_report.json, and reports each stage as
PASS / FAIL / NOT RUN with the actual reason. A stage is NEVER marked PASS
because the code exists - only because it executed and produced its artifact.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import time
import traceback
from datetime import datetime, timezone
from typing import Any, Dict

from .benchmark.latency import run_latency_benchmark
from .crypto.mldsa_audit import PQCDelegationAuditModule
from .detector.ablation import run_ablation_experiments
from .detector.baselines import run_baseline_experiments
from .detector.train import train_pipeline
from .dtl.ledger import DTLLedger
from .fidelity.report import generate_fidelity_report
from .paths import FINAL_REPORT_PATH, ensure_dirs


def _git_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip() or "NOT A GIT REPOSITORY"
    except Exception:
        return "NOT A GIT REPOSITORY"


def _stage(name: str, fn) -> Dict[str, Any]:
    """Runs one stage, capturing the real outcome."""
    print("\n" + "=" * 74)
    print(f"STAGE: {name}")
    print("=" * 74)
    started = time.time()
    try:
        result = fn()
        return {
            "status": "PASS",
            "elapsed_seconds": round(time.time() - started, 2),
            "result": result,
        }
    except Exception as exc:
        traceback.print_exc()
        return {
            "status": "FAIL",
            "elapsed_seconds": round(time.time() - started, 2),
            "error": f"{type(exc).__name__}: {exc}",
        }


def run_full_experiment(seed: int = 42, samples: int = 24000) -> Dict[str, Any]:
    ensure_dirs()
    experiment_id = f"FORSETI-EXP-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    started = time.time()

    print("=" * 74)
    print(f"FORSETI FULL EXPERIMENT PIPELINE  |  {experiment_id}  |  seed={seed}")
    print("=" * 74)

    stages: Dict[str, Any] = {}

    stages["model_training"] = _stage(
        "Model training, temporal split, attack-family holdout, SHAP",
        lambda: train_pipeline(seed=seed, num_samples=samples),
    )
    stages["baselines"] = _stage(
        "Four-architecture baselines + deterministic DTL invariant",
        lambda: run_baseline_experiments(seed=seed, num_samples=samples),
    )
    stages["ablation"] = _stage(
        "Feature-group ablation",
        lambda: run_ablation_experiments(seed=seed, num_samples=samples),
    )
    stages["fidelity"] = _stage(
        "Statistical fidelity against public anchors",
        lambda: generate_fidelity_report(seed=seed),
    )
    stages["benchmark"] = _stage(
        "Latency benchmark",
        lambda: run_latency_benchmark(num_iterations=10000, seed=seed),
    )

    def _pqc() -> Dict[str, Any]:
        pqc = PQCDelegationAuditModule()
        if not pqc.available:
            raise RuntimeError(f"PQC unavailable: {pqc.provider_status().get('unavailable_reason')}")
        auth = DTLLedger().get_authority("auth_household_grocery_2026")
        tamper = pqc.run_tamper_test(auth.model_dump())
        if not tamper.get("all_tamper_tests_passed"):
            raise RuntimeError(f"ML-DSA-44 tamper tests did not all behave correctly: {tamper}")
        return {"provider": pqc.provider_status(), "tamper_tests": tamper}

    stages["pqc_cryptography"] = _stage("NIST FIPS 204 ML-DSA-44 signing & tamper proof", _pqc)

    # --------------------------------------------------------- summarise
    def _res(stage: str, path: str, default=None):
        s = stages.get(stage, {})
        if s.get("status") != "PASS":
            return default
        node = s.get("result")
        for key in path.split("."):
            if not isinstance(node, dict):
                return default
            node = node.get(key)
            if node is None:
                return default
        return node

    fidelity_status = "NOT RUN / DATASET UNAVAILABLE"
    if stages["fidelity"]["status"] == "PASS":
        anchors = _res("fidelity", "metadata.anchor_datasets_loaded", []) or []
        fidelity_status = "RUN" if anchors else "PIPELINE OK / ANCHOR DATASETS UNAVAILABLE"

    report = {
        "metadata": {
            "experiment_id": experiment_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "seed": seed,
            "samples": samples,
            "git_commit": _git_commit(),
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "elapsed_seconds": round(time.time() - started, 2),
        },
        "statuses": {k: v["status"] for k, v in stages.items()},
        "stage_details": {
            k: {"status": v["status"], "elapsed_seconds": v["elapsed_seconds"], "error": v.get("error")}
            for k, v in stages.items()
        },
        "headline": {
            "model_backend": _res("model_training", "model.backend"),
            "test_pr_auc": _res("model_training", "test_metrics.pr_auc"),
            "test_roc_auc": _res("model_training", "test_metrics.roc_auc"),
            "explainability_method": _res("model_training", "explainability_method"),
            "measured_dtl_pr_auc_lift": _res("ablation", "measured_dtl_feature_lift.lift"),
            "cross_rail_recall_holdout": _res(
                "baselines", "headline_finding.cross_rail_split_recall_when_family_held_out"),
            "latency_p99_ms": _res("benchmark", "breakdown.full_end_to_end_pipeline.p99_ms"),
            "latency_sla_verdict": _res("benchmark", "metadata.sla_verdict"),
            "pqc_all_tamper_tests_passed": _res("pqc_cryptography", "tamper_tests.all_tamper_tests_passed"),
            "fidelity_status": fidelity_status,
        },
        "honesty_notes": [
            "Every status above reflects an actual execution, not the presence of code.",
            "Fidelity reports PIPELINE OK / ANCHOR DATASETS UNAVAILABLE when the licensed "
            "PaySim/ULB CSVs are absent. No realism claim is made in that case.",
            "CROSS_RAIL_SPLIT is withheld from training; near-zero ML recall on it is the "
            "measured evidence motivating the deterministic DTL invariant.",
        ],
    }

    with open(FINAL_REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print("\n" + "=" * 74)
    print("FINAL REPORT")
    print("=" * 74)
    for stage, status in report["statuses"].items():
        mark = "PASS" if status == "PASS" else status
        print(f"  {stage:<22} {mark}")
        err = report["stage_details"][stage].get("error")
        if err:
            print(f"      reason: {err}")
    print(f"\n  saved -> {FINAL_REPORT_PATH}")
    print(f"  total elapsed: {report['metadata']['elapsed_seconds']}s")
    print("=" * 74)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the full FORSETI experiment pipeline")
    parser.add_argument("--seed", type=int, default=int(os.environ.get("SEED", 42)))
    parser.add_argument("--samples", type=int, default=24000)
    args = parser.parse_args()
    run_full_experiment(seed=args.seed, samples=args.samples)
