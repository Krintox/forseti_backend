from typing import List, Dict, Optional, Any
from pydantic import BaseModel, Field
from datetime import datetime

class SemanticDriftProof(BaseModel):
    proof_id: str
    authority_id: str
    tx_id: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    
    # Invariant Details
    invariant_code: str  # e.g., "INV_01_GLOBAL_BUDGET_EXCEEDED" or "INV_02_SEMANTIC_INTENT_DRIFT"
    severity: str = "CRITICAL" # LOW, MEDIUM, HIGH, CRITICAL
    
    # Semantic breakdown
    authorized_intent_summary: str
    actual_cart_summary: str
    drift_score: float # 0.0 (exact match) to 1.0 (complete violation)
    violated_skus: List[str] = Field(default_factory=list)
    
    # Cross-rail state breakdown
    local_rail_statuses: Dict[str, str] = Field(default_factory=dict)
    cumulative_spend_before: float
    attempted_amount: float
    global_ceiling: float
    total_exposure_after: float
    
    # Mathematical proof & explanation
    invariant_expression: str
    explanation: str
    
    # Post-quantum signature verification
    pqc_signature_suite: str = "ML-DSA-44 (NIST FIPS 204)"
    pqc_signature_bytes_hex: str = "4a8f9c1e2b..."
    pqc_verified: bool = True

class StateConsistencyProof(BaseModel):
    proof_id: str
    authority_id: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    participating_rails: List[str]
    joint_state_hash: str
    is_joint_state_reachable: bool
    discrepancy_details: str
    logical_clock_tick: int
