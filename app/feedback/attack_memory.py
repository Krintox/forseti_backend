from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from datetime import datetime

class AttackAttemptRecord(BaseModel):
    round_id: int
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    strategy: str
    target_rails: List[str]
    attempted_amount: float
    is_detected: bool
    detection_score: float
    violating_invariant: Optional[str] = None
    defense_action: str
    red_reasoning: str
    blue_adaptation: str

class AttackMemoryStore:
    """
    Stateful memory store for closed-loop Red/Blue interactions.
    """
    def __init__(self):
        self.history: List[AttackAttemptRecord] = []

    def record_attempt(self, record: AttackAttemptRecord):
        self.history.append(record)

    def get_latest_attempt(self) -> Optional[AttackAttemptRecord]:
        return self.history[-1] if self.history else None

    def get_blocked_strategies(self) -> List[str]:
        return [r.strategy for r in self.history if r.is_detected and r.defense_action != "ALLOW"]

    def clear(self):
        self.history = []
