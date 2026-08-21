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

    # Device fingerprint the agent transacted from. Populated by the synthetic
    # dataset generator to feed Payment Graph Sentinel's device-sharing
    # feature (see graph_sentinel/); demo/red-team vectors leave it unset.
    device_id: Optional[str] = None
    
    # CHIMERA Global Evaluation
    is_anomalous_red_attack: bool = False
    attack_primitive_type: Optional[str] = None
    violation_flag: bool = False
    semantic_drift_score: float = 0.0
    containment_action: Optional[str] = None

    # ---- Deception Lab: fields modelling an attack on the AGENT'S OWN
    # reasoning rather than on a payment rail or the DTL. None of these are
    # ever trusted by any deterministic check elsewhere in the system - that
    # is precisely the property the deception_lab package exists to prove.
    # A compromised merchant/tool result the agent's LLM layer was exposed
    # to. Free text, never parsed as an instruction by anything downstream.
    injected_payload: Optional[str] = None
    # What a (possibly poisoned) product-search tool told the agent the
    # category was - as distinct from the ground-truth CartItem categories
    # below, which are what the DTL actually checks.
    tool_reported_category: Optional[str] = None
    # A fabricated "the user already approved a higher limit" claim smuggled
    # into the agent's context/memory.
    claimed_prior_authorization: Optional[Dict[str, Any]] = None
    # True if the agent that INITIATED this transaction is also the one
    # recorded as having APPROVED the authority escalation behind it, with no
    # separate principal in the loop.
    self_approved: bool = False
    approving_agent_id: Optional[str] = None
