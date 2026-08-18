import json
import os
from typing import List, Dict, Optional, Any
from datetime import datetime, date
from ..models.transactions import SyntheticTransaction
from ..models.state import TransactionState

def json_serial(obj):
    """JSON serializer for objects not serializable by default json code"""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if hasattr(obj, "value"):
        return obj.value
    raise TypeError(f"Type {type(obj)} not serializable")

class AppendOnlyEventLog:
    """
    Append-Only Event Log for all synthetic payment and delegation events.
    Enables deterministic replay and immutable audit trails.
    """
    def __init__(self, log_file_path: Optional[str] = None):
        self.log_file_path = log_file_path or os.path.join(
            os.path.dirname(__file__), "..", "..", "data", "event_log.jsonl"
        )
        os.makedirs(os.path.dirname(self.log_file_path), exist_ok=True)
        self.events: List[Dict] = []
        self._load_existing_log()

    def _load_existing_log(self):
        if os.path.exists(self.log_file_path):
            try:
                with open(self.log_file_path, "r", encoding="utf-8") as f:
                    for line in f:
                        if line.strip():
                            self.events.append(json.loads(line.strip()))
            except Exception:
                self.events = []

    def append_event(self, event_type: str, payload: Dict):
        event = {
            "event_id": f"evt_{len(self.events) + 1:06d}",
            "timestamp": datetime.utcnow().isoformat(),
            "event_type": event_type,
            "payload": payload
        }
        self.events.append(event)
        with open(self.log_file_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, default=json_serial) + "\n")
        return event

    def clear(self):
        self.events = []
        if os.path.exists(self.log_file_path):
            with open(self.log_file_path, "w", encoding="utf-8") as f:
                f.write("")

    def get_events_for_authority(self, authority_id: str) -> List[Dict]:
        return [
            e for e in self.events 
            if e.get("payload", {}).get("authority_id") == authority_id
        ]
