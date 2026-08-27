"""
Canonical filesystem locations for FORSETI.

Every module resolves artifact paths through here. Previously several modules
computed their own relative paths and disagreed about how many levels up the
repository root was, so the training pipeline wrote to backend/artifacts/ while
the API read from ./artifacts/ and silently served stale fallbacks.
"""

from __future__ import annotations

import os

_APP_DIR = os.path.dirname(os.path.abspath(__file__))          # .../app
_BACKEND_DIR = os.path.abspath(os.path.join(_APP_DIR, ".."))    # .../backend
_PARENT_DIR = os.path.abspath(os.path.join(_BACKEND_DIR, "..")) # monorepo root, when nested


def _resolve_artifacts_dir() -> str:
    """
    Find the artifact tree in BOTH layouts this code runs in.

    Locally the backend lives inside the monorepo, so artifacts sit one level
    above it (`Forseti/artifacts/`). Deployed, the backend repository IS the
    deployment root, so `..` from it points outside the checkout entirely and
    `<parent>/artifacts` does not exist - the API then silently fell back to
    whatever it could find, or to nothing.

    Preference order, most specific first:

      1. $ARTIFACTS_DIR              - an explicit deployment override wins
      2. <backend>/artifacts         - the copy that actually ships
      3. <monorepo root>/artifacts   - where the pipeline writes locally

    A directory only counts if it holds `evaluation/`, so an empty stub left by
    `ensure_dirs()` cannot shadow a real tree.
    """
    override = os.environ.get("ARTIFACTS_DIR")
    if override:
        return os.path.abspath(override)

    candidates = [
        os.path.join(_BACKEND_DIR, "artifacts"),
        os.path.join(_PARENT_DIR, "artifacts"),
    ]
    for candidate in candidates:
        if os.path.isdir(os.path.join(candidate, "evaluation")):
            return candidate
    return candidates[0]


ARTIFACTS_DIR = _resolve_artifacts_dir()

# Everything else still hangs off the monorepo root when it exists, falling back
# to the backend directory when the backend is deployed on its own.
REPO_ROOT = _PARENT_DIR if os.path.isdir(os.path.join(_PARENT_DIR, "docs")) else _BACKEND_DIR
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
