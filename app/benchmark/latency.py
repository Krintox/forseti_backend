"""
Latency benchmark for the inline detection path.

Measures wall-clock execution of the real components. The SLA verdict is
COMPUTED from the measurement, never asserted.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import numpy as np

from ..detector.feature_schema import DTLFeatureExtractor
from ..detector.inference import HybridMLDetectorInference
from ..dtl.invariant_engine import DTLInvariantEngine
from ..dtl.ledger import DTLLedger
from ..models.state import PaymentRailType
from ..models.transactions import CartItem, SyntheticTransaction
from ..paths import BENCHMARK_DIR, LATENCY_PATH

# Inline authorization budget we hold ourselves to. Stated as an assumption,
# not as an industry fact.
INLINE_SLA_MS = 30.0


def _stats(latencies: List[float]) -> Dict[str, float]:
    arr = np.asarray(latencies, dtype=float)
    return {
        "p50_ms": round(float(np.percentile(arr, 50)), 4),
        "p95_ms": round(float(np.percentile(arr, 95)), 4),
        "p99_ms": round(float(np.percentile(arr, 99)), 4),
        "mean_ms": round(float(arr.mean()), 4),
        "min_ms": round(float(arr.min()), 4),
        "max_ms": round(float(arr.max()), 4),
        "n": int(len(arr)),
    }


def run_latency_benchmark(
    num_iterations: int = 10000,
    seed: int = 42,
    output_path: Optional[str] = None,
) -> Dict[str, Any]:
    print("=" * 74)
    print(f"LATENCY BENCHMARK  |  {num_iterations} transactions  |  seed={seed}")
    print("=" * 74)

    rng = np.random.default_rng(seed)
    ledger = DTLLedger()
    auth = ledger.get_authority("auth_household_grocery_2026")
    invariant_engine = DTLInvariantEngine()
    detector = HybridMLDetectorInference()

    if detector.model is None:
        print("  WARNING: no trained model artifact loaded; ML timings would not reflect the")
        print("           production inference path. Run `python -m app.detector.train` first.")

    rails = [PaymentRailType.CARD_TOKEN, PaymentRailType.UPI_CIRCLE, PaymentRailType.AGENTIC_AP2]
    transactions = [
        SyntheticTransaction(
            tx_id=f"tx_bench_{i:06d}",
            authority_id=auth.authority_id,
            agent_id=auth.agent_id,
            rail=rails[i % len(rails)],
            amount=round(float(rng.uniform(500.0, 4500.0)), 2),
            merchant_id=f"merch_{i % 20}",
            merchant_name=f"Merchant {i % 20}",
            merchant_mcc="5411" if i % 3 != 0 else "5734",
            items=[CartItem(sku="SKU_BENCH", name="Test Item", category="GROCERY", unit_price=500.0, quantity=1)],
        )
        for i in range(num_iterations)
    ]

    # Warm up JIT/caches so the first measured call is not an outlier.
    for tx in transactions[: min(100, len(transactions))]:
        DTLFeatureExtractor.extract_features(auth, tx)
        invariant_engine.evaluate_invariants(auth, tx)
        detector.evaluate_transaction(auth, tx)

    lat_feature: List[float] = []
    lat_dtl: List[float] = []
    lat_ml: List[float] = []
    lat_full: List[float] = []

    print(f"Measuring {num_iterations} iterations...")
    for tx in transactions:
        t0 = time.perf_counter()

        tf0 = time.perf_counter()
        DTLFeatureExtractor.extract_features(auth, tx)
        tf1 = time.perf_counter()

        td0 = time.perf_counter()
        invariant_engine.evaluate_invariants(auth, tx)
        td1 = time.perf_counter()

        tm0 = time.perf_counter()
        detector.evaluate_transaction(auth, tx)
        tm1 = time.perf_counter()

        t1 = time.perf_counter()

        lat_feature.append((tf1 - tf0) * 1000.0)
        lat_dtl.append((td1 - td0) * 1000.0)
        lat_ml.append((tm1 - tm0) * 1000.0)
        lat_full.append((t1 - t0) * 1000.0)

    full = _stats(lat_full)
    sla_met = full["p99_ms"] < INLINE_SLA_MS

    results: Dict[str, Any] = {
        "metadata": {
            "benchmark_id": f"BENCH-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "seed": seed,
            "iterations_measured": num_iterations,
            "model_loaded": detector.model is not None,
            "model_backend": getattr(detector, "backend_name", "unknown"),
            "explainability_in_measured_path": getattr(detector, "explain_in_hot_path", False),
            "host": {
                "platform": platform.platform(),
                "processor": platform.processor() or "unknown",
                "python": platform.python_version(),
            },
            "inline_sla_budget_ms": INLINE_SLA_MS,
            "sla_note": "Self-imposed inline authorization budget, not a published network requirement.",
            "sla_p99_met": bool(sla_met),
            "sla_verdict": ("PASS - measured p99 %.4f ms < %.1f ms budget" % (full["p99_ms"], INLINE_SLA_MS))
            if sla_met
            else ("FAIL - measured p99 %.4f ms >= %.1f ms budget" % (full["p99_ms"], INLINE_SLA_MS)),
        },
        "breakdown": {
            "feature_extraction": _stats(lat_feature),
            "dtl_invariant_evaluation": _stats(lat_dtl),
            "ml_inference": _stats(lat_ml),
            "full_end_to_end_pipeline": full,
        },
    }

    print("\nMeasured latency (ms):")
    for name, block in results["breakdown"].items():
        print(f"  {name:<28} p50={block['p50_ms']:<9} p95={block['p95_ms']:<9} p99={block['p99_ms']:<9} max={block['max_ms']}")
    print(f"\n  {results['metadata']['sla_verdict']}")

    os.makedirs(BENCHMARK_DIR, exist_ok=True)
    out = output_path or LATENCY_PATH
    with open(out, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    print(f"  saved -> {out}")
    print("=" * 74)
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the FORSETI latency benchmark")
    parser.add_argument("--iterations", type=int, default=10000)
    parser.add_argument("--seed", type=int, default=int(os.environ.get("SEED", 42)))
    args = parser.parse_args()
    run_latency_benchmark(num_iterations=args.iterations, seed=args.seed)
