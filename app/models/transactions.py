from typing import List, Dict, Optional, Any
from pydantic import BaseModel, Field
from datetime import datetime
from .state import PaymentRailType, TransactionState

class CartItem(BaseModel):
    sku: str
    name: str
    category: str
    unit_price: float
    quantity: int = 1
    is_stored_value: bool = False
    attributes: Dict[str, Any] = Field(default_factory=dict)

    @property
    def total_price(self) -> float:
        return self.unit_price * self.quantity

class SyntheticTransaction(BaseModel):
    tx_id: str
    authority_id: str
    agent_id: str
    rail: PaymentRailType
    amount: float
    currency: str = "INR"
    merchant_id: str
    merchant_name: str
    merchant_mcc: str
    items: List[CartItem] = Field(default_factory=list)
    state: TransactionState = TransactionState.INITIATED
    
    # Signatures & Cryptographic tokens
    client_signature: str = "ed25519_sig_valid"
    intent_mandate_hash: Optional[str] = None
    cart_mandate_hash: Optional[str] = None
    dpan_token: Optional[str] = None
    vpa_delegate: Optional[str] = None
    
    # Timing
    created_at: datetime = Field(default_factory=datetime.utcnow)
    authorized_at: Optional[datetime] = None
    settled_at: Optional[datetime] = None
    
    # Local Rail Decision metadata
    local_rail_status: str = "PENDING"
    local_rail_message: str = ""
    
    # CHIMERA Global Evaluation
    is_anomalous_red_attack: bool = False
    attack_primitive_type: Optional[str] = None
    violation_flag: bool = False
    semantic_drift_score: float = 0.0
    containment_action: Optional[str] = None
