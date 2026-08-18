from abc import ABC, abstractmethod
from typing import Tuple
from ...models.transactions import SyntheticTransaction
from ...models.state import TransactionState

class BaseRailAdapter(ABC):
    """
    Base interface for payment rail adapters.
    Each adapter enforces local syntactic validation, token scopes, and local ceilings.
    """
    def __init__(self, rail_name: str, local_limit: float = 10000.0):
        self.rail_name = rail_name
        self.local_limit = local_limit
        self.local_spent: float = 0.0

    @abstractmethod
    def validate_and_authorize_local(self, tx: SyntheticTransaction) -> Tuple[bool, str]:
        """
        Validates local syntactic rules (MCC, local limit, signatures).
        Returns (is_authorized_locally, reason_message)
        """
        pass

    @abstractmethod
    def capture_local(self, tx: SyntheticTransaction) -> bool:
        pass

    def reset_local_state(self):
        self.local_spent = 0.0
