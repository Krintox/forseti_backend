"""
Canonical filesystem locations for FORSETI.

Every module resolves artifact paths through here. Previously several modules
computed their own relative paths and disagreed about how many levels up the
repository root was, so the training pipeline wrote to backend/artifacts/ while
the API read from ./artifacts/ and silently served stale fallbacks.
"""

from __future__ import annotations

import os

# backend/app/paths.py -> backend/app -> backend -> <repo root>
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

ARTIFACTS_DIR = os.environ.get("ARTIFACTS_DIR", os.path.join(REPO_ROOT, "artifacts"))
MODELS_DIR = os.path.join(ARTIFACTS_DIR, "models")
EVALUATION_DIR = os.path.join(ARTIFACTS_DIR, "evaluation")
FIDELITY_DIR = os.path.join(ARTIFACTS_DIR, "fidelity")
BENCHMARK_DIR = os.path.join(ARTIFACTS_DIR, "benchmark")
PQC_DIR = os.path.join(ARTIFACTS_DIR, "pqc")
EVENTS_DIR = os.path.join(ARTIFACTS_DIR, "events")

DATASET_DIR = os.environ.get("DATASET_DIR", os.path.join(REPO_ROOT, "data", "anchors"))
DOCS_DIR = os.path.join(REPO_ROOT, "docs")

MODEL_PATH = os.environ.get("MODEL_PATH", os.path.join(MODELS_DIR, "forseti_model.joblib"))
FEATURE_SCHEMA_PATH = os.path.join(MODELS_DIR, "feature_schema.json")
METRICS_PATH = os.path.join(EVALUATION_DIR, "metrics.json")
BASELINES_PATH = os.path.join(EVALUATION_DIR, "baselines.json")
ABLATION_PATH = os.path.join(EVALUATION_DIR, "ablation_results.json")
FIDELITY_REPORT_PATH = os.path.join(FIDELITY_DIR, "fidelity_report.json")
LATENCY_PATH = os.path.join(BENCHMARK_DIR, "latency.json")
FINAL_REPORT_PATH = os.path.join(ARTIFACTS_DIR, "final_report.json")


def ensure_dirs() -> None:
    for d in (ARTIFACTS_DIR, MODELS_DIR, EVALUATION_DIR, FIDELITY_DIR, BENCHMARK_DIR, PQC_DIR, EVENTS_DIR):
        os.makedirs(d, exist_ok=True)
