from typing import Dict, List, Tuple, Optional
from ..models.transactions import SyntheticTransaction
from ..models.state import PaymentRailType, TransactionState
from .adapters.base import BaseRailAdapter
from .adapters.card_adapter import CardTokenAdapter
from .adapters.upi_adapter import UpiCircleAdapter
from .adapters.agentic_adapter import AgenticAp2Adapter
from .event_log import AppendOnlyEventLog

class PaymentSimulatorEngine:
    """
    Generic Payment Simulation Engine.
    Executes standard lifecycle (INIT -> AUTH -> CAPTURE -> SETTLE) across 3 adapters.
    Maintains an in-memory projection and appends raw events to the JSONL log.
    """
    def __init__(self, event_log: Optional[AppendOnlyEventLog] = None):
        self.event_log = event_log or AppendOnlyEventLog()
        # Each rail carries the limits its real counterpart actually enforces,
        # not a uniform placeholder. UPI Circle in particular mandates
        # Rs 5,000/transaction and Rs 15,000/month for a delegated secondary
        # user; modelling it at a flat Rs 10,000 with no per-transaction cap
        # made the simulated rail MORE permissive than the real one and let the
        # project claim a control the real scheme already ships.
        self.adapters: Dict[PaymentRailType, BaseRailAdapter] = {
            PaymentRailType.CARD_TOKEN: CardTokenAdapter(local_limit=10000.0),
            PaymentRailType.UPI_CIRCLE: UpiCircleAdapter(
                local_limit=UpiCircleAdapter.SCHEME_MONTHLY_LIMIT,
                per_tx_limit=UpiCircleAdapter.SCHEME_PER_TX_LIMIT,
            ),
            PaymentRailType.AGENTIC_AP2: AgenticAp2Adapter(local_limit=10000.0)
        }
        self.transactions: Dict[str, SyntheticTransaction] = {}

    def process_transaction_local(self, tx: SyntheticTransaction) -> Tuple[bool, str]:
        """
        Submits transaction to the requested rail adapter for local authorization.
        """
        adapter = self.adapters.get(tx.rail)
        if not adapter:
            tx.state = TransactionState.FAILED
            tx.local_rail_status = "REJECTED_UNKNOWN_RAIL"
            tx.local_rail_message = f"Unsupported payment rail: {tx.rail}"
            return False, tx.local_rail_message

        # Log event initiation
        self.event_log.append_event("TX_INITIATED", tx.model_dump())
        self.transactions[tx.tx_id] = tx

        # Execute local adapter check
        is_auth, msg = adapter.validate_and_authorize_local(tx)
        
        # Log auth outcome
        if is_auth:
            self.event_log.append_event("TX_LOCAL_AUTHORIZED", tx.model_dump())
        else:
            tx.state = TransactionState.FAILED
            self.event_log.append_event("TX_LOCAL_REJECTED", tx.model_dump())

        return is_auth, msg

    def capture_and_settle(self, tx_id: str) -> bool:
        tx = self.transactions.get(tx_id)
        if not tx:
            return False
        adapter = self.adapters.get(tx.rail)
        if adapter and adapter.capture_local(tx):
            self.event_log.append_event("TX_SETTLED", tx.model_dump())
            return True
        return False

    def reset_all_rails(self):
        for adapter in self.adapters.values():
            adapter.reset_local_state()
        self.transactions.clear()
        self.event_log.clear()
