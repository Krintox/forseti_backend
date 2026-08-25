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
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .arena.events import Actor, ArenaEvent, EventRecorder, EventType
from .arena.orchestrator import AUTHORITY_ID, ArenaBattleOrchestrator
from .crypto.mldsa_audit import PQCDelegationAuditModule
from .demo_runner import run_live_demo
from .models.transactions import CartItem, SyntheticTransaction
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
from .kill_chain import KILL_CHAIN_STAGES
from .kill_chain import coverage as kill_chain_coverage
from .models.state import PaymentRailType
from .taxonomy import TAXONOMY, taxonomy_summary
from .tokenization import TokenStore, activate, check_and_expire, issue_token, revoke, use_token

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
token_store = TokenStore()

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


class CampaignRequest(BaseModel):
    round_numbers: Optional[List[int]] = None
    dtl_enabled: bool = True
    speed: float = 1.0


class LimitRequest(BaseModel):
    delegated_limit: float


class ResetRequest(BaseModel):
    delegated_limit: Optional[float] = None


class AuthorityScopeRequest(BaseModel):
    """
    The non-monetary dimensions of a delegation. All optional - an operator
    typically changes one dimension at a time (e.g. "UPI only") while leaving
    the ceiling untouched.
    """
    global_budget_ceiling: Optional[float] = None
    permitted_rails: Optional[List[str]] = None
    per_transaction_cap: Optional[float] = None
    permitted_mccs: Optional[List[str]] = None
    validity_window_hours: Optional[float] = None
    economic_purpose: Optional[str] = None
    beneficiary_scope: Optional[List[str]] = None


class PQCVerifyRequest(BaseModel):
    snapshot_payload: Dict[str, Any]
    signature_hex: Optional[str] = None


class TokenIssueRequest(BaseModel):
    agent_id: str = "agent_household_butler"
    principal_id: str = "user_household_principal"
    scope: str = "Household groceries and consumables"
    validity_hours: float = 24.0
    amount_ceiling: Optional[float] = None
    per_transaction_limit: Optional[float] = None
    allowed_rails: Optional[List[str]] = None
    merchant_scope: Optional[List[str]] = None
    purpose_scope: Optional[str] = None


class TokenRevokeRequest(BaseModel):
    reason: str = "REVOKED_BY_PRINCIPAL"


class TokenUseRequest(BaseModel):
    rail: str
    amount: float
    merchant_id: str = "merch_probe"
    merchant_name: str = "Probe Merchant"
    merchant_mcc: str = "5411"
    category: str = "GROCERY"


def load_artifact(path: str) -> Dict[str, Any]:
    """Reads an experiment artifact, or reports honestly that it is missing."""
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            data["_artifact"] = {
                "status": "LOADED",
                # Forward slashes even on Windows: browsers give backslash-joined
                # paths no line-break opportunity, which forced the provenance
                # footer to blow out its container width on real demo laptops.
                "path": os.path.relpath(path, os.path.dirname(DOCS_DIR)).replace(os.sep, "/"),
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


@app.post("/api/arena/authority-scope")
def set_authority_scope(req: AuthorityScopeRequest) -> Dict[str, Any]:
    """
    Applies non-monetary dimensions of the delegation - permitted rails,
    per-transaction cap, MCC scope, validity window, purpose - on top of the
    live grant. The ceiling alone cannot express "₹12,000, UPI only"; this is
    the route that lets an operator actually state that.
    """
    profile: Dict[str, Any] = req.model_dump(exclude_none=True)
    if "permitted_rails" in profile:
        try:
            profile["permitted_rails"] = [PaymentRailType(r) for r in profile["permitted_rails"]]
        except ValueError as exc:
            return {"status": "REJECTED", "reason": f"unknown rail in permitted_rails: {exc}"}
    return orchestrator.set_authority_scope(profile)


@app.get("/api/arena/authority-vector")
def get_authority_vector() -> Dict[str, Any]:
    """The full grant, one row per authority dimension - what INV_01..INV_06 each guard."""
    state = orchestrator.get_state()
    return {"authority_vector": state["authority_vector"], "invariant_registry": state["invariant_registry"]}


@app.get("/api/arena/intent-firewall")
def get_intent_firewall_verdicts() -> Dict[str, Any]:
    """
    Per-transaction drift vectors + ALLOW/PARTIAL_DRIFT/HARD_DRIFT verdicts
    from the most recently completed round. Empty until a round has run.
    """
    verdicts = orchestrator.last_firewall_verdicts
    hard = sum(1 for v in verdicts if v.get("verdict") == "HARD_DRIFT")
    partial = sum(1 for v in verdicts if v.get("verdict") == "PARTIAL_DRIFT")
    return {
        "verdicts": verdicts,
        "count": len(verdicts),
        "hard_drift_count": hard,
        "partial_drift_count": partial,
        "allow_count": len(verdicts) - hard - partial,
    }


@app.get("/api/arena/deception-lab")
def get_deception_lab_verdicts() -> Dict[str, Any]:
    """
    Per-transaction Deception Lab detections from the most recently completed
    round. These target the AGENT's reasoning (prompt injection, tool-output
    poisoning, context poisoning, authority impersonation), not the delegated
    authority ledger - a transaction can be CLEAN here yet still violate the
    grant (see /api/arena/intent-firewall), or vice versa.
    """
    verdicts = orchestrator.last_deception_verdicts
    detected = sum(1 for v in verdicts if v.get("verdict") == "DECEPTION_DETECTED")
    return {
        "verdicts": verdicts,
        "count": len(verdicts),
        "detected_count": detected,
        "clean_count": len(verdicts) - detected,
    }


@app.get("/api/arena/kill-chain")
def get_kill_chain() -> Dict[str, Any]:
    """
    The 11-stage lifecycle taxonomy, the last round's score against it, and
    this session's cumulative stage coverage across every round run since the
    last reset.
    """
    last = orchestrator.last_round.get("kill_chain") if orchestrator.last_round else None
    return {
        "stages": KILL_CHAIN_STAGES,
        "last_round": last,
        "session_coverage": kill_chain_coverage(orchestrator.round_history),
    }


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


@app.post("/api/arena/campaign")
async def execute_arena_campaign(req: CampaignRequest) -> Dict[str, Any]:
    """
    Runs a sequence of rounds back to back (default: RAIL_SCOPE_VIOLATION
    three times), so the Blue escalation ladder and kill-chain coverage are
    visible across a whole campaign, not just one round. Events for every
    round in the sequence stream over the same WebSocket as /api/arena/round.
    """
    async def stream_cb(event_data: Dict[str, Any]) -> None:
        await manager.broadcast(event_data)

    return await orchestrator.run_campaign(
        round_numbers=req.round_numbers,
        dtl_enabled=req.dtl_enabled,
        event_callback=stream_cb,
        speed=req.speed,
    )


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
def list_recordings(limit: int = 40) -> Dict[str, Any]:
    """Most recent recordings, newest first. Bounded so the replay panel does
    not re-read every round ever recorded on each poll."""
    return {"recordings": EventRecorder.list_recordings(limit=limit)}


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


# --------------------------------------------------------- tokenization
#
# Synthetic scoped-token model demonstrating how a tokenized payment
# credential inherits and enforces delegated authority - NOT a real network
# token vault (see app/tokenization/lifecycle.py). A token's own scope is
# clamped to the live DTL authority at issuance, and every use is
# independently re-checked against that authority's CURRENT state:
#
#     TOKEN SCOPE  ->  DTL AUTHORITY  ->  PAYMENT ACTION


@app.post("/api/tokens/issue")
def issue_payment_token(req: TokenIssueRequest) -> Dict[str, Any]:
    auth = orchestrator.ledger.get_authority(AUTHORITY_ID)
    allowed_rails = None
    if req.allowed_rails:
        allowed_rails = [PaymentRailType(r) for r in req.allowed_rails]
    token = issue_token(
        auth,
        agent_id=req.agent_id, principal_id=req.principal_id, scope=req.scope,
        validity_hours=req.validity_hours, amount_ceiling=req.amount_ceiling,
        per_transaction_limit=req.per_transaction_limit,
        allowed_rails=allowed_rails, merchant_scope=req.merchant_scope,
        purpose_scope=req.purpose_scope,
    )
    activate(token)
    token_store.add(token)
    return {"status": "ISSUED", "token": json.loads(token.model_dump_json())}


@app.get("/api/tokens")
def list_payment_tokens() -> Dict[str, Any]:
    tokens = [check_and_expire(t) for t in token_store.list()]
    return {"tokens": [json.loads(t.model_dump_json()) for t in tokens]}


@app.get("/api/tokens/{token_id}")
def get_payment_token(token_id: str) -> Dict[str, Any]:
    token = token_store.get(token_id)
    if token is None:
        return {"status": "NOT_FOUND", "token_id": token_id}
    check_and_expire(token)
    return {"status": "FOUND", "token": json.loads(token.model_dump_json())}


@app.post("/api/tokens/{token_id}/revoke")
def revoke_payment_token(token_id: str, req: TokenRevokeRequest) -> Dict[str, Any]:
    token = token_store.get(token_id)
    if token is None:
        return {"status": "NOT_FOUND", "token_id": token_id}
    revoke(token, req.reason)
    return {"status": "REVOKED", "token": json.loads(token.model_dump_json())}


@app.post("/api/tokens/{token_id}/use")
def use_payment_token(token_id: str, req: TokenUseRequest) -> Dict[str, Any]:
    """
    Tests a hypothetical transaction against a token's scope AND the live DTL
    authority behind it - the demo surface for TOKEN SCOPE -> DTL AUTHORITY ->
    PAYMENT ACTION. Nothing here books real exposure against the arena; a
    successful use only advances the TOKEN's own cumulative_used/status.
    """
    token = token_store.get(token_id)
    if token is None:
        return {"status": "NOT_FOUND", "token_id": token_id}
    auth = orchestrator.ledger.get_authority(AUTHORITY_ID)
    tx = SyntheticTransaction(
        tx_id=f"tx_token_probe_{uuid.uuid4().hex[:8]}",
        authority_id=auth.authority_id, agent_id=token.agent_id,
        rail=PaymentRailType(req.rail), amount=req.amount,
        merchant_id=req.merchant_id, merchant_name=req.merchant_name, merchant_mcc=req.merchant_mcc,
        items=[CartItem(sku="SKU_PROBE", name="Probe item", category=req.category,
                         unit_price=req.amount, quantity=1)],
    )
    ok, violation = use_token(token, auth, tx)
    return {
        "status": "ALLOWED" if ok else "TOKEN_SCOPE_VIOLATION",
        "ok": ok,
        "violation": json.loads(violation.model_dump_json()) if violation else None,
        "token": json.loads(token.model_dump_json()),
    }


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
