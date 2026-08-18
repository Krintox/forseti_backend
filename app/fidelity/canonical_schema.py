from typing import Optional, Dict, Any
from pydantic import BaseModel, Field
from datetime import datetime

class CanonicalTransaction(BaseModel):
    """
    Canonical Transaction Schema for Statistical Fidelity Comparison.
    Provides standard representation across PaySim, ULB, and Synthetic datasets.
    Missing fields are explicitly set to None (never fabricated).
    """
    transaction_id: str
    timestamp: Optional[datetime] = None
    amount: float
    currency: str = "INR"
    merchant_category: Optional[str] = None
    rail: Optional[str] = None
    device_id: Optional[str] = None
    customer_id: Optional[str] = None
    fraud_label: int = Field(default=0, ge=0, le=1)
    
    # Dataset origin tag
    dataset_source: str = "SYNTHETIC"
