import pytest
from app.simulator.state_machine import PaymentSimulatorEngine
from app.models.transactions import SyntheticTransaction, CartItem
from app.models.state import PaymentRailType, TransactionState

def test_simulator_local_authorization():
    sim = PaymentSimulatorEngine()
    
    # Valid Grocery transaction on Card Token adapter
    tx = SyntheticTransaction(
        tx_id="test_tx_001",
        authority_id="auth_test",
        agent_id="agent_test",
        rail=PaymentRailType.CARD_TOKEN,
        amount=2500.0,
        merchant_id="merch_test",
        merchant_name="Nature's Basket",
        merchant_mcc="5411",
        items=[CartItem(sku="SKU_MILK", name="Milk", category="GROCERY", unit_price=2500.0, quantity=1)]
    )
    
    is_auth, msg = sim.process_transaction_local(tx)
    assert is_auth is True
    assert tx.state == TransactionState.AUTHORIZED
    assert "APPROVED_LOCALLY" in tx.local_rail_status

def test_simulator_rail_limit_enforcement():
    sim = PaymentSimulatorEngine()
    
    # Transaction exceeding single rail ₹10k ceiling
    tx = SyntheticTransaction(
        tx_id="test_tx_002",
        authority_id="auth_test",
        agent_id="agent_test",
        rail=PaymentRailType.UPI_CIRCLE,
        amount=15000.0,
        merchant_id="merch_test",
        merchant_name="BigBasket",
        merchant_mcc="5411",
        items=[CartItem(sku="SKU_LARGE", name="Bulk Grocery", category="GROCERY", unit_price=15000.0, quantity=1)]
    )
    
    is_auth, msg = sim.process_transaction_local(tx)
    assert is_auth is False
    assert tx.state == TransactionState.FAILED
