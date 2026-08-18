"""
Structured event model for the FORSETI arena.

Every meaningful backend action emits one of these envelopes. The SAME object is
(1) appended to the in-memory round log, (2) written to a JSONL file for replay,
and (3) broadcast over the WebSocket. The frontend animation is driven entirely
by this stream, so what the judge sees on screen is literally what the backend
did - there is no separate scripted animation timeline.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


class EventType(str, Enum):
    # lifecycle
    ROUND_STARTED = "ROUND_STARTED"
    ATTACK_STARTED = "ATTACK_STARTED"
    ATTACK_STEP = "ATTACK_STEP"
    ATTACK_COMPLETE = "ATTACK_COMPLETE"
    ROUND_COMPLETE = "ROUND_COMPLETE"
    # rail interaction
    RAIL_REQUEST = "RAIL_REQUEST"
    RAIL_APPROVED = "RAIL_APPROVED"
    RAIL_DECLINED = "RAIL_DECLINED"
    # delegation trust ledger
    DTL_EVALUATION = "DTL_EVALUATION"
    DTL_EXPOSURE_UPDATED = "DTL_EXPOSURE_UPDATED"
    INVARIANT_VIOLATION = "INVARIANT_VIOLATION"
    # detection
    ML_SCORE = "ML_SCORE"
    SHAP_EXPLANATION = "SHAP_EXPLANATION"
    # response
    POLICY_DECISION = "POLICY_DECISION"
    PARTIAL_AUTH = "PARTIAL_AUTH"
    QUARANTINE = "QUARANTINE"
    CAPABILITY_REDUCTION = "CAPABILITY_REDUCTION"
    # audit
    PQC_SIGN = "PQC_SIGN"
    PQC_VERIFY = "PQC_VERIFY"
    # closed loop
    RED_ADAPTATION = "RED_ADAPTATION"
    BLUE_ADAPTATION = "BLUE_ADAPTATION"
    EVALUATION_COMPLETE = "EVALUATION_COMPLETE"


class Actor(str, Enum):
    RED_AGENT = "RED_AGENT"
    CARD_RAIL = "CARD_RAIL"
    UPI_RAIL = "UPI_RAIL"
    AGENTIC_RAIL = "AGENTIC_RAIL"
    DTL = "DTL"
    ML_DETECTOR = "ML_DETECTOR"
    COST_GOVERNOR = "COST_GOVERNOR"
    PQC_AUDITOR = "PQC_AUDITOR"
    SYSTEM = "SYSTEM"


RAIL_TO_ACTOR = {
    "CARD_TOKEN": Actor.CARD_RAIL,
    "UPI_CIRCLE": Actor.UPI_RAIL,
    "AGENTIC_AP2": Actor.AGENTIC_RAIL,
}

_seq = itertools.count(1)


@dataclass
class ArenaEvent:
    """One backend action, in a shape the UI can render directly."""

    event_type: str
    actor: str
    payload: Dict[str, Any] = field(default_factory=dict)
    experiment_id: str = ""
    round_id: int = 0
    step: int = 0
    # Short text the UI paints ON the animated arrow.
    arrow_label: str = ""
    # Where the animation travels: logical node ids the frontend knows.
    source: Optional[str] = None
    target: Optional[str] = None
    severity: str = "info"  # info | success | warning | critical
    event_id: str = field(default_factory=lambda: f"evt_{uuid.uuid4().hex[:12]}")
    sequence: int = field(default_factory=lambda: next(_seq))
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    monotonic_ms: float = field(default_factory=lambda: round(time.perf_counter() * 1000.0, 3))

    def __post_init__(self) -> None:
        # EventType/Actor are str-enums; store their plain values so every
        # consumer (JSON, WebSocket, replay file) sees the same literal.
        self.event_type = getattr(self.event_type, "value", self.event_type)
        self.actor = getattr(self.actor, "value", self.actor)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class EventRecorder:
    """
    Keeps the ordered event log for a run and persists it as JSONL so a round
    can be replayed later with its original relative timing.
    """

    def __init__(self, experiment_id: Optional[str] = None, persist_dir: Optional[str] = None):
        self.experiment_id = experiment_id or f"ARENA-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
        self.events: List[Dict[str, Any]] = []
        self._t0: Optional[float] = None
        # Genesis of the hash chain. Every appended entry commits to the one
        # before it, so the log is tamper-EVIDENT rather than merely append-only.
        self.head_hash: str = "0" * 64
        if persist_dir is None:
            from ..paths import EVENTS_DIR

            persist_dir = EVENTS_DIR
        self.persist_dir = persist_dir
        os.makedirs(self.persist_dir, exist_ok=True)
        self.path = os.path.join(self.persist_dir, f"{self.experiment_id}.jsonl")

    def record(self, event: ArenaEvent) -> Dict[str, Any]:
        """Stamps the event with elapsed time and appends it to the log."""
        if self._t0 is None:
            self._t0 = event.monotonic_ms
        data = event.to_dict()
        data["offset_ms"] = round(event.monotonic_ms - self._t0, 3)

        # entry_hash = H(prev_hash || canonical(entry)). Recomputing the chain
        # detects any edit, reorder or deletion anywhere in the history.
        data["prev_hash"] = self.head_hash
        body = json.dumps({k: v for k, v in data.items() if k != "entry_hash"},
                          sort_keys=True, separators=(",", ":"), default=str)
        data["entry_hash"] = hashlib.sha256((self.head_hash + body).encode("utf-8")).hexdigest()
        self.head_hash = data["entry_hash"]

        self.events.append(data)
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(data) + "\n")
        except Exception:
            # Persistence is best-effort; a disk problem must never break a demo.
            pass
        return data

    def reset(self, experiment_id: Optional[str] = None) -> None:
        self.experiment_id = experiment_id or f"ARENA-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
        self.events = []
        self._t0 = None
        self.head_hash = "0" * 64
        self.path = os.path.join(self.persist_dir, f"{self.experiment_id}.jsonl")

    def log_root(self) -> str:
        """Current head of the hash chain - the value the PQC layer signs."""
        return self.head_hash

    @staticmethod
    def verify_chain(events: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Recomputes the chain over a recorded log and reports the first entry
        that does not match. Used by /api/arena/verify-log.
        """
        prev = "0" * 64
        for idx, entry in enumerate(events):
            if entry.get("prev_hash") != prev:
                return {"valid": False, "broken_at_index": idx,
                        "reason": "prev_hash does not match the preceding entry",
                        "event_id": entry.get("event_id")}
            body = json.dumps({k: v for k, v in entry.items() if k != "entry_hash"},
                              sort_keys=True, separators=(",", ":"), default=str)
            expected = hashlib.sha256((prev + body).encode("utf-8")).hexdigest()
            if expected != entry.get("entry_hash"):
                return {"valid": False, "broken_at_index": idx,
                        "reason": "entry content does not match its recorded hash",
                        "event_id": entry.get("event_id")}
            prev = expected
        return {"valid": True, "entries_verified": len(events), "log_root": prev}

    def timeline(self) -> List[Dict[str, Any]]:
        return list(self.events)

    @staticmethod
    def load(path: str) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        if not os.path.exists(path):
            return out
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return out

    @staticmethod
    def list_recordings(persist_dir: Optional[str] = None) -> List[Dict[str, Any]]:
        if persist_dir is None:
            from ..paths import EVENTS_DIR

            persist_dir = EVENTS_DIR
        if not os.path.isdir(persist_dir):
            return []
        rows = []
        for name in sorted(os.listdir(persist_dir), reverse=True):
            if not name.endswith(".jsonl"):
                continue
            full = os.path.join(persist_dir, name)
            try:
                rows.append({
                    "experiment_id": name[:-6],
                    "path": full,
                    "event_count": sum(1 for _ in open(full, encoding="utf-8")),
                    "modified": datetime.fromtimestamp(os.path.getmtime(full), timezone.utc).isoformat(),
                })
            except Exception:
                continue
        return rows
