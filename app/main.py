"""
FORSETI API.

Metric endpoints serve ONLY what the experiment pipelines actually wrote to
artifacts/. When an artifact is missing the response carries
status="NOT RUN / ARTIFACT UNAVAILABLE" plus the command that produces it. No
endpoint substitutes a plausible-looking number for a missing measurement.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .arena.events import Actor, ArenaEvent, EventRecorder, EventType
from .arena.orchestrator import ArenaBattleOrchestrator
from .crypto.mldsa_audit import PQCDelegationAuditModule
from .demo_runner import run_live_demo
from .paths import (
    ABLATION_PATH,
    BASELINES_PATH,
    DOCS_DIR,
    FIDELITY_REPORT_PATH,
    FINAL_REPORT_PATH,
    LATENCY_PATH,
    METRICS_PATH,
    ensure_dirs,
)
from .ai.routes import register as register_ai_routes
from .taxonomy import TAXONOMY, taxonomy_summary

app = FastAPI(
    title="FORSETI Defense Lab API",
    description="Delegation-Trust Ledger, adversarial arena, and reproducible evaluation surface.",
    version="3.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ensure_dirs()
orchestrator = ArenaBattleOrchestrator()
pqc_module = PQCDelegationAuditModule()

# AI agent layer. Advisory only - never in the enforcement path.
register_ai_routes(app, orchestrator)

# How each artifact is regenerated, surfaced when it is missing.
ARTIFACT_COMMANDS = {
    METRICS_PATH: "python -m app.detector.train --seed 42",
    BASELINES_PATH: "python -m app.detector.baselines --seed 42",
    ABLATION_PATH: "python -m app.detector.ablation --seed 42",
    FIDELITY_REPORT_PATH: "python -m app.fidelity.report",
    LATENCY_PATH: "python -m app.benchmark.latency --iterations 10000",
    FINAL_REPORT_PATH: "python -m app.experiment_runner --seed 42",
}


def jsonable(value: Any) -> Any:
    """
    Recursively coerces datetimes, enums and pydantic models into primitives.

    WebSocket frames are encoded with plain json.dumps, which - unlike FastAPI's
    response encoder - does not understand datetime. A single datetime buried in
    a payload used to raise inside send_json and take the connection down.
    """
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(jsonable(k)): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(v) for v in value]
    if hasattr(value, "model_dump"):
        try:
            return jsonable(value.model_dump(mode="json"))
        except Exception:
            pass
    if hasattr(value, "value"):  # enum
        return jsonable(value.value)
    return str(value)


class ConnectionManager:
    def __init__(self) -> None:
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: Dict[str, Any]) -> None:
        # Serialize ONCE, outside the per-connection loop: if a payload is not
        # JSON-encodable we must find out here rather than silently dropping
        # every subscriber, which is exactly how the live stream broke before.
        try:
            text = json.dumps(jsonable(message))
        except Exception as exc:  # pragma: no cover - defensive
            print(f"[ws] refusing to broadcast unserializable event: {exc}")
            return

        dead: List[WebSocket] = []
        for connection in list(self.active_connections):
            try:
                await connection.send_text(text)
            except Exception:
                dead.append(connection)
        for d in dead:
            self.disconnect(d)


manager = ConnectionManager()


class RoundRequest(BaseModel):
    round_number: int = 2
    dtl_enabled: bool = True
    speed: float = 1.0
    strategy: Optional[str] = None


class LimitRequest(BaseModel):
    delegated_limit: float


class ResetRequest(BaseModel):
    delegated_limit: Optional[float] = None


class PQCVerifyRequest(BaseModel):
    snapshot_payload: Dict[str, Any]
    signature_hex: Optional[str] = None


def load_artifact(path: str) -> Dict[str, Any]:
    """Reads an experiment artifact, or reports honestly that it is missing."""
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            data["_artifact"] = {
                "status": "LOADED",
                "path": os.path.relpath(path, os.path.dirname(DOCS_DIR)),
                "generated_at": datetime.fromtimestamp(os.path.getmtime(path), timezone.utc).isoformat(),
            }
            return data
        except Exception as exc:
            return {
                "status": "ARTIFACT UNREADABLE",
                "error": f"{type(exc).__name__}: {exc}",
                "path": path,
                "regenerate_with": ARTIFACT_COMMANDS.get(path),
            }
    return {
        "status": "NOT RUN / ARTIFACT UNAVAILABLE",
        "path": path,
        "regenerate_with": ARTIFACT_COMMANDS.get(path, "see docs/reproducibility.md"),
        "note": "No measurement exists for this yet. Nothing is substituted.",
    }


@app.get("/")
def get_root() -> Dict[str, Any]:
    return {
        "project": "FORSETI",
        "challenge": "Mastercard Innovation Challenge @ GFF 2026",
        "status": "ONLINE",
        "version": "3.0.0",
        "core_claim": "A delegated agent may act only within granted authority, regardless of which payment rail it uses.",
        "honesty_policy": "Every metric served here is read from an artifact produced by an executable pipeline.",
    }


@app.get("/api/health")
def health() -> Dict[str, Any]:
    detector = orchestrator.detector.status()
    pqc = pqc_module.provider_status()
    artifacts = {
        "metrics": os.path.exists(METRICS_PATH),
        "baselines": os.path.exists(BASELINES_PATH),
        "ablation": os.path.exists(ABLATION_PATH),
        "fidelity": os.path.exists(FIDELITY_REPORT_PATH),
        "latency": os.path.exists(LATENCY_PATH),
    }
    return {
        "api": "OK",
        "model_loaded": detector["model_loaded"],
        "model_backend": detector["backend"],
        "genuine_shap": detector["is_genuine_shap"],
        "pqc_available": pqc["available"],
        "pqc_backend": pqc["backend"],
        "artifacts_present": artifacts,
        "artifacts_missing": [k for k, v in artifacts.items() if not v],
    }


# ------------------------------------------------------------------ arena


@app.get("/api/arena/state")
def get_arena_state() -> Dict[str, Any]:
    return orchestrator.get_state()


@app.post("/api/arena/limit")
def set_limit(req: LimitRequest) -> Dict[str, Any]:
    """Applies an operator-entered delegated ceiling."""
    return orchestrator.set_delegated_limit(req.delegated_limit)


@app.post("/api/arena/reset")
def reset_arena(req: Optional[ResetRequest] = None) -> Dict[str, Any]:
    budget = req.delegated_limit if req and req.delegated_limit is not None else None
    state = orchestrator.reset(budget)
    return {"status": "RESET_OK", **state}


@app.post("/api/arena/round")
async def execute_arena_round(req: RoundRequest) -> Dict[str, Any]:
    async def stream_cb(event_data: Dict[str, Any]) -> None:
        await manager.broadcast(event_data)

    result = await orchestrator.run_round_stream(
        round_number=req.round_number,
        dtl_enabled=req.dtl_enabled,
        event_callback=stream_cb,
        speed=req.speed,
        strategy_override=req.strategy,
    )
    # Route the closing frame through the recorder so it carries a real
    # event_id/sequence and joins the hash chain. Hand-built dicts here used to
    # ship without an event_id, which gave React duplicate empty keys.
    closing = ArenaEvent(
        event_type=EventType.ROUND_COMPLETE,
        actor=Actor.SYSTEM,
        experiment_id=result["experiment_id"],
        round_id=req.round_number,
        arrow_label=f"ROUND {req.round_number} COMPLETE - {result['winner']} WINS",
        severity="success" if result["winner"] == "BLUE" else "critical",
        payload={
            "winner": result["winner"],
            "detected": result["detected"],
            "strategy": result["strategy"],
        },
    )
    await manager.broadcast(orchestrator.recorder.record(closing))
    return result


@app.get("/api/arena/events")
def get_arena_events() -> Dict[str, Any]:
    return {
        "experiment_id": orchestrator.recorder.experiment_id,
        "events": orchestrator.recorder.timeline(),
    }


@app.get("/api/arena/verify-log")
def verify_event_log() -> Dict[str, Any]:
    """Recomputes the event hash chain and reports whether it is intact."""
    events = orchestrator.recorder.timeline()
    result = EventRecorder.verify_chain(events)
    result["experiment_id"] = orchestrator.recorder.experiment_id
    result["head"] = orchestrator.recorder.log_root()
    return result


@app.get("/api/arena/recordings")
def list_recordings() -> Dict[str, Any]:
    return {"recordings": EventRecorder.list_recordings()}


@app.get("/api/arena/replay/{experiment_id}")
def get_replay(experiment_id: str) -> Dict[str, Any]:
    from .paths import EVENTS_DIR

    path = os.path.join(EVENTS_DIR, f"{experiment_id}.jsonl")
    events = EventRecorder.load(path)
    if not events:
        return {"status": "NOT FOUND", "experiment_id": experiment_id, "events": []}
    return {
        "status": "LOADED",
        "experiment_id": experiment_id,
        "event_count": len(events),
        "duration_ms": events[-1].get("offset_ms", 0),
        "events": events,
    }


@app.post("/api/demo/start")
def start_live_demo() -> Dict[str, Any]:
    return run_live_demo()


# ------------------------------------------------------------- evaluation


@app.get("/api/metrics")
def get_metrics() -> Dict[str, Any]:
    return load_artifact(METRICS_PATH)


@app.get("/api/evaluation")
def get_evaluation() -> Dict[str, Any]:
    return {
        "metrics": load_artifact(METRICS_PATH),
        "baselines": load_artifact(BASELINES_PATH),
        "ablation": load_artifact(ABLATION_PATH),
    }


@app.get("/api/fidelity")
def get_fidelity() -> Dict[str, Any]:
    return load_artifact(FIDELITY_REPORT_PATH)


@app.get("/api/benchmark/latency")
def get_latency() -> Dict[str, Any]:
    return load_artifact(LATENCY_PATH)


@app.get("/api/report/final")
def get_final_report() -> Dict[str, Any]:
    return load_artifact(FINAL_REPORT_PATH)


@app.get("/api/explainability")
def get_explainability() -> Dict[str, Any]:
    """Global SHAP importance from the trained model, plus its provenance."""
    metrics = load_artifact(METRICS_PATH)
    status = orchestrator.detector.status()
    shap_global = metrics.get("shap_global", {}) if isinstance(metrics, dict) else {}
    return {
        "detector": status,
        "shap_global": shap_global,
        "method": shap_global.get("method", status.get("explainability_method")),
        "is_genuine_shap": bool(shap_global.get("available")) and shap_global.get("method") == "shap.TreeExplainer",
        "source_experiment": metrics.get("experiment_id") if isinstance(metrics, dict) else None,
    }


# --------------------------------------------------------------- taxonomy


@app.get("/api/attacks")
def get_taxonomy() -> Dict[str, Any]:
    return {"summary": taxonomy_summary(), "vectors": TAXONOMY}


# -------------------------------------------------------------------- pqc


@app.get("/api/pqc/status")
def get_pqc_status() -> Dict[str, Any]:
    auth = orchestrator.ledger.get_authority("auth_household_grocery_2026")
    provider = pqc_module.provider_status()
    if not provider["available"]:
        return {
            "provider": provider,
            "status": "PQC MODULE UNAVAILABLE",
            "latest_snapshot": None,
            "tamper_verification_results": {"tests_run": False},
        }
    snapshot = pqc_module.create_signed_snapshot(auth.model_dump())
    return {
        "provider": provider,
        "status": snapshot["verification_status"],
        "latest_snapshot": snapshot,
        "tamper_verification_results": pqc_module.run_tamper_test(auth.model_dump()),
    }


@app.post("/api/pqc/verify")
def verify_pqc(req: PQCVerifyRequest) -> Dict[str, Any]:
    provider = pqc_module.provider_status()
    if not provider["available"]:
        return {"is_signature_valid": False, "verification_status": "PQC MODULE UNAVAILABLE",
                "reason": provider.get("unavailable_reason")}
    is_valid = pqc_module.verify_snapshot(req.snapshot_payload, req.signature_hex)
    return {
        "is_signature_valid": is_valid,
        "algorithm": provider["algorithm"],
        "backend": provider["backend"],
        "verification_status": "ML-DSA-44 VERIFIED" if is_valid else "VERIFICATION FAILED",
    }


# --------------------------------------------------------------- feedback


@app.get("/api/feedback/history")
def get_feedback_history() -> Dict[str, Any]:
    auth = orchestrator.ledger.get_authority("auth_household_grocery_2026")
    ratio = (auth.authority_headroom / auth.global_budget_ceiling) if auth.global_budget_ceiling > 0 else 0.0
    return {
        "history": orchestrator.feedback_engine.get_adaptation_history(),
        "next_plan": orchestrator.feedback_engine.plan_next_strategy(headroom_ratio=ratio),
    }


# -------------------------------------------------------------- websocket


@app.websocket("/ws/arena")
async def websocket_arena(websocket: WebSocket) -> None:
    await manager.connect(websocket)
    try:
        await websocket.send_text(json.dumps(jsonable({
            "event_type": "CONNECTED",
            "actor": "SYSTEM",
            "arrow_label": "LIVE EVENT STREAM CONNECTED",
            "severity": "success",
            "payload": orchestrator.get_state(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })))
        while True:
            # Keeps the socket open; the server pushes, the client mostly listens.
            msg = await websocket.receive_text()
            if msg == "ping":
                await websocket.send_text(json.dumps({
                    "event_type": "PONG", "actor": "SYSTEM",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }))
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as exc:  # pragma: no cover - defensive
        print(f"[ws] connection dropped: {type(exc).__name__}: {exc}")
        manager.disconnect(websocket)
