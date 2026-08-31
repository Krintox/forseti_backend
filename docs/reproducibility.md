# Research Reproducibility & Execution Guide

## Quick Start Commands

### 1. Research Mode: Full End-to-End Pipeline
Executes synthetic dataset generation, temporal training, 4-baseline benchmarking, feature ablation, SciPy statistical fidelity, 1,000-tx latency benchmarking, NIST FIPS 204 ML-DSA-44 signing/verification, and compiles `artifacts/final_report.json`:

```bash
cd backend
python -m app.experiment_runner --seed 42 --samples 12000
```

### 2. Demo Mode: Instant Presentation (<2 seconds)
Loads pre-compiled verified model and executes the 8-phase deterministic live pitch demonstration:

```bash
cd backend
python -m app.demo_runner --seed 42
```

### 3. Automated Backend Test Suite
Runs all 8 unit and integration tests (DTL Two-Phase balance, invariants, feature extraction, ML inference, PQC tamper tests, closed-loop feedback):

```bash
cd backend
python tests/verify_all.py
```

### 4. Interactive Live UI & Judge Mode
Start backend:
```bash
cd backend
python -m uvicorn app.main:app --port 8000 --host 0.0.0.0
```

Start frontend:
```bash
cd frontend
npm run dev -- -p 3005
```

Navigate to `http://localhost:3005` and click **"ENTER JUDGE MODE"** for the 6-phase guided pitch.
