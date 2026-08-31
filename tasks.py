"""
Cross-platform task runner (a `make` stand-in for Windows).

    python tasks.py all          # full reproducible pipeline
    python tasks.py train        # train the detector
    python tasks.py test         # run the test suite
    python tasks.py list         # show every target

Each target shells out to the same commands the Makefile uses, so the two
never drift apart.
"""

from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.join(ROOT, "backend")
FRONTEND = os.path.join(ROOT, "frontend")
SEED = os.environ.get("SEED", "42")
SAMPLES = os.environ.get("SAMPLES", "24000")
PY = sys.executable

TASKS = {
    "install":   (BACKEND,  [PY, "-m", "pip", "install", "-r", "requirements.txt"],
                  "install python dependencies"),
    "train":     (BACKEND,  [PY, "-m", "app.detector.train", "--seed", SEED, "--samples", SAMPLES],
                  "train the detector (temporal split + attack-family holdout)"),
    "evaluate":  (BACKEND,  [PY, "-m", "app.detector.baselines", "--seed", SEED, "--samples", SAMPLES],
                  "four-architecture baseline benchmark"),
    "ablation":  (BACKEND,  [PY, "-m", "app.detector.ablation", "--seed", SEED, "--samples", SAMPLES],
                  "feature-group ablation study"),
    "fidelity":  (BACKEND,  [PY, "-m", "app.fidelity.report"],
                  "statistical fidelity against public anchors"),
    "benchmark": (BACKEND,  [PY, "-m", "app.benchmark.latency", "--iterations", "10000", "--seed", SEED],
                  "measure inline latency over 10,000 transactions"),
    "pqc-test":  (BACKEND,  [PY, "-m", "pytest", "tests/test_forseti.py", "-k", "TestPQC", "-v"],
                  "prove ML-DSA-44 signing and tamper detection"),
    "test":      (BACKEND,  [PY, "-m", "pytest", "tests/", "-q"],
                  "run the full test suite"),
    "demo":      (BACKEND,  [PY, "-m", "app.demo_runner", "--seed", SEED],
                  "deterministic flagship demo"),
    "all":       (BACKEND,  [PY, "-m", "app.experiment_runner", "--seed", SEED, "--samples", SAMPLES],
                  "full reproducible pipeline -> artifacts/final_report.json"),
    "experiment": (BACKEND, [PY, "-m", "app.experiment_runner", "--seed", SEED, "--samples", SAMPLES],
                   "alias for `all`"),
    "anchors":   (ROOT,     [PY, "scripts/download_anchors.py"],
                  "show anchor dataset status and sources"),
    "backend":   (BACKEND,  [PY, "-m", "uvicorn", "app.main:app", "--port", "8000", "--host", "0.0.0.0"],
                  "serve the API on :8000"),
    "frontend":  (FRONTEND, ["npm", "run", "dev", "--", "-p", "3005"],
                  "serve the dashboard on :3005"),
}


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] in ("list", "help", "-h", "--help"):
        print("FORSETI targets (SEED=%s SAMPLES=%s)\n" % (SEED, SAMPLES))
        for name, (_, _, desc) in TASKS.items():
            print(f"  {name:<12} {desc}")
        return 0

    name = sys.argv[1]
    if name not in TASKS:
        print(f"unknown target '{name}'. Run `python tasks.py list`.")
        return 1

    cwd, cmd, desc = TASKS[name]
    print(f"==> {name}: {desc}")
    print(f"    {' '.join(cmd)}  (cwd={os.path.relpath(cwd, ROOT) or '.'})\n")
    return subprocess.call(cmd, cwd=cwd, shell=(os.name == "nt" and cmd[0] == "npm"))


if __name__ == "__main__":
    sys.exit(main())
