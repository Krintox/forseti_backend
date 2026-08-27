from typing import List, Dict, Optional, Any
from pydantic import BaseModel, Field, model_validator
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
    #
    # Left unset, this is FILLED IN by signing the transaction's canonical
    # economic content (see simulator/client_signing.py). It used to default to
    # the literal string "ed25519_sig_valid", and the rails "verified" it by
    # checking whether the word "invalid" appeared in it - a field the
    # transaction set about itself, which no tampering could ever break.
    #
    # Now: a well-formed client signs what it is asking for, and changing the
    # amount, merchant, rail, beneficiary or cart after the fact invalidates
    # the tag. To model a bad signature, tamper with a signed transaction (or
    # write junk here) rather than declaring it bad.
    client_signature: Optional[str] = None
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

    # ---- Containment outcome, STRUCTURED.
    #
    # `containment_action` is an English sentence for a human. It was also the
    # only place the numbers lived: a transaction contained by a headroom cap
    # carried `amount = 6000` (what was attempted) with the actually-authorised
    # Rs 3,000 recoverable only by parsing prose. Anything downstream wanting
    # "how much really cleared?" had to regex a sentence or recompute it.
    #
    # `amount` deliberately still holds the ATTEMPTED figure - that is evidence,
    # and rewriting it is what the cost governor was caught doing once already.
    # These fields record what the governor decided about it.
    containment_code: Optional[str] = None      # RAIL_SCOPE_BLOCK, PARTIAL_AUTH, ...
    authorized_amount: Optional[float] = None   # what actually cleared
    quarantined_amount: Optional[float] = None  # what was held back

    # ---- Settlement Reconciliation: fields modelling the post-authorization
    # lifecycle (see app/settlement/). Two legs sharing an obligation_id model
    # ONE authorised economic obligation whose settlement is attempted more
    # than once - the failure mode a per-transaction DTL invariant cannot see,
    # because each leg is evaluated (and authorised) independently and
    # correctly at the time it is presented.
    obligation_id: Optional[str] = None
    settlement_action: Optional[str] = None  # CAPTURE | REFUND | DUPLICATE_CAPTURE

    @model_validator(mode="after")
    def _sign_if_unsigned(self) -> "SyntheticTransaction":
        """
        An honest client signs what it is asking for.

        Signing here rather than at every construction site means the DEFAULT
        state of a transaction is "signed for exactly these terms", so a rail's
        verification is a real check on every path, and a generator has to opt
        IN to producing a bad signature (by tampering after construction, or by
        writing junk into the field) instead of opting in to a good one.

        Imported lazily: simulator.client_signing type-hints this module.
        """
        if self.client_signature is None:
            from ..simulator.client_signing import sign

            object.__setattr__(self, "client_signature", sign(self))
        return self
