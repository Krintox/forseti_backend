"""
Tests for the synthetic tokenized-payment credential lifecycle
(app/tokenization/). The central property under test throughout: a token
must never be able to authorize an action outside the DTL delegation it was
issued from - neither at issuance (clamped scope) nor at use (re-checked
against the LIVE authority, not just the token's own static fields).
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.models.state import DTLGlobalAuthorityState, PaymentRailType
from app.models.transactions import CartItem, SyntheticTransaction
from app.tokenization import (
    TokenStatus,
    activate,
    check_and_expire,
    issue_token,
    revoke,
    use_token,
)
from app.tokenization.store import TokenStore


def _auth(**overrides) -> DTLGlobalAuthorityState:
    base = dict(
        authority_id="auth_test", principal="test_principal", agent_id="agent_test",
        global_budget_ceiling=10000.0,
    )
    base.update(overrides)
    return DTLGlobalAuthorityState(**base)


def _tx(**overrides) -> SyntheticTransaction:
    base = dict(
        tx_id="tx_001", authority_id="auth_test", agent_id="agent_test",
        rail=PaymentRailType.CARD_TOKEN, amount=1000.0,
        merchant_id="merch_x", merchant_name="Merchant X", merchant_mcc="5411",
        items=[CartItem(sku="SKU_X", name="Item X", category="GROCERY", unit_price=1000.0, quantity=1)],
    )
    base.update(overrides)
    return SyntheticTransaction(**base)


class TestIssuance:
    def test_issued_token_is_clamped_to_authority_headroom(self):
        auth = _auth(global_budget_ceiling=5000.0)
        token = issue_token(auth, agent_id="agent_test", principal_id="user_1",
                             scope="groceries", amount_ceiling=999999.0)
        assert token.amount_ceiling == auth.authority_headroom == 5000.0

    def test_issued_token_cannot_request_a_rail_outside_the_delegation(self):
        auth = _auth(permitted_rails=[PaymentRailType.UPI_CIRCLE])
        token = issue_token(
            auth, agent_id="agent_test", principal_id="user_1", scope="groceries",
            allowed_rails=[PaymentRailType.UPI_CIRCLE, PaymentRailType.CARD_TOKEN, PaymentRailType.AGENTIC_AP2],
        )
        assert token.allowed_rails == [PaymentRailType.UPI_CIRCLE]

    def test_issued_token_validity_cannot_outlive_the_delegation(self):
        auth = _auth(validity_window_hours=1.0)
        token = issue_token(auth, agent_id="agent_test", principal_id="user_1",
                             scope="groceries", validity_hours=999.0)
        assert token.expires_at <= auth.expires_at + timedelta(seconds=1)

    def test_starts_in_issued_status(self):
        auth = _auth()
        token = issue_token(auth, agent_id="agent_test", principal_id="user_1", scope="groceries")
        assert token.status == TokenStatus.ISSUED


class TestActivation:
    def test_activate_transitions_issued_to_active(self):
        auth = _auth()
        token = issue_token(auth, agent_id="agent_test", principal_id="user_1", scope="groceries")
        activate(token)
        assert token.status == TokenStatus.ACTIVE

    def test_cannot_activate_a_non_issued_token(self):
        auth = _auth()
        token = issue_token(auth, agent_id="agent_test", principal_id="user_1", scope="groceries")
        activate(token)
        with pytest.raises(ValueError):
            activate(token)


class TestUseWithinScope:
    def test_valid_use_succeeds_and_books_exposure(self):
        auth = _auth(global_budget_ceiling=10000.0)
        token = activate(issue_token(auth, agent_id="agent_test", principal_id="user_1",
                                      scope="groceries", amount_ceiling=5000.0))
        ok, violation = use_token(token, auth, _tx(amount=1000.0))
        assert ok is True
        assert violation is None
        assert token.cumulative_used == 1000.0
        assert token.use_count == 1
        assert token.status == TokenStatus.ACTIVE

    def test_token_becomes_used_when_ceiling_exhausted(self):
        auth = _auth(global_budget_ceiling=10000.0)
        token = activate(issue_token(auth, agent_id="agent_test", principal_id="user_1",
                                      scope="groceries", amount_ceiling=1000.0))
        ok, _ = use_token(token, auth, _tx(amount=1000.0))
        assert ok is True
        assert token.status == TokenStatus.USED

    def test_used_token_refuses_further_use(self):
        auth = _auth(global_budget_ceiling=10000.0)
        token = activate(issue_token(auth, agent_id="agent_test", principal_id="user_1",
                                      scope="groceries", amount_ceiling=1000.0))
        use_token(token, auth, _tx(amount=1000.0))
        ok, violation = use_token(token, auth, _tx(tx_id="tx_002", amount=1.0))
        assert ok is False
        assert violation.violation_code == "TOKEN_EXHAUSTED"


class TestTokenScopeViolations:
    def test_rail_outside_token_scope_is_refused(self):
        auth = _auth()
        token = activate(issue_token(auth, agent_id="agent_test", principal_id="user_1",
                                      scope="groceries", allowed_rails=[PaymentRailType.UPI_CIRCLE]))
        ok, violation = use_token(token, auth, _tx(rail=PaymentRailType.CARD_TOKEN))
        assert ok is False
        assert violation.violation_code == "TOKEN_RAIL_OUT_OF_SCOPE"

    def test_merchant_outside_token_scope_is_refused(self):
        auth = _auth()
        token = activate(issue_token(auth, agent_id="agent_test", principal_id="user_1",
                                      scope="groceries", merchant_scope=["5411"]))
        ok, violation = use_token(token, auth, _tx(merchant_mcc="7995"))
        assert ok is False
        assert violation.violation_code == "TOKEN_MERCHANT_OUT_OF_SCOPE"

    def test_per_transaction_limit_is_enforced(self):
        auth = _auth()
        token = activate(issue_token(auth, agent_id="agent_test", principal_id="user_1",
                                      scope="groceries", per_transaction_limit=500.0))
        ok, violation = use_token(token, auth, _tx(amount=600.0))
        assert ok is False
        assert violation.violation_code == "TOKEN_PER_TX_LIMIT_EXCEEDED"

    def test_amount_over_remaining_ceiling_is_refused(self):
        auth = _auth()
        token = activate(issue_token(auth, agent_id="agent_test", principal_id="user_1",
                                      scope="groceries", amount_ceiling=500.0))
        ok, violation = use_token(token, auth, _tx(amount=600.0))
        assert ok is False
        assert violation.violation_code == "TOKEN_AMOUNT_CEILING_EXCEEDED"


class TestTokenCannotOutliveTheLiveDelegation:
    """The property the master spec calls out explicitly: TOKEN SCOPE -> DTL AUTHORITY -> PAYMENT ACTION."""

    def test_use_is_refused_when_the_live_authority_rail_scope_is_narrowed_after_issuance(self):
        auth = _auth(permitted_rails=[PaymentRailType.CARD_TOKEN, PaymentRailType.UPI_CIRCLE])
        token = activate(issue_token(auth, agent_id="agent_test", principal_id="user_1", scope="groceries"))
        assert PaymentRailType.UPI_CIRCLE in token.allowed_rails  # token's own scope still allows it

        # The delegation is narrowed AFTER the token was minted.
        auth.permitted_rails = [PaymentRailType.CARD_TOKEN]

        ok, violation = use_token(token, auth, _tx(rail=PaymentRailType.UPI_CIRCLE))
        assert ok is False
        assert violation.violation_code == "TOKEN_EXCEEDS_LIVE_DTL_AUTHORITY"

    def test_use_is_refused_when_live_headroom_has_been_consumed_elsewhere(self):
        auth = _auth(global_budget_ceiling=10000.0)
        token = activate(issue_token(auth, agent_id="agent_test", principal_id="user_1",
                                      scope="groceries", amount_ceiling=8000.0))
        # Something else consumes most of the live headroom after issuance.
        auth.cumulative_spent_settled = 9500.0
        ok, violation = use_token(token, auth, _tx(amount=1000.0))
        assert ok is False
        assert violation.violation_code == "TOKEN_EXCEEDS_LIVE_DTL_AUTHORITY"

    def test_revoked_delegation_token_cannot_transact(self):
        auth = _auth()
        token = activate(issue_token(auth, agent_id="agent_test", principal_id="user_1", scope="groceries"))
        revoke(token, "REVOKED_BY_PRINCIPAL")
        ok, violation = use_token(token, auth, _tx())
        assert ok is False
        assert violation.violation_code == "TOKEN_REVOKED"


class TestExpiry:
    def test_expired_token_is_refused_and_marked_expired(self):
        auth = _auth()
        token = activate(issue_token(auth, agent_id="agent_test", principal_id="user_1", scope="groceries"))
        token.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
        ok, violation = use_token(token, auth, _tx())
        assert ok is False
        assert violation.violation_code == "TOKEN_EXPIRED"
        assert token.status == TokenStatus.EXPIRED

    def test_check_and_expire_is_idempotent_on_terminal_statuses(self):
        auth = _auth()
        token = activate(issue_token(auth, agent_id="agent_test", principal_id="user_1", scope="groceries"))
        revoke(token, "REVOKED_BY_PRINCIPAL")
        token.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
        check_and_expire(token)
        assert token.status == TokenStatus.REVOKED  # revoked stays revoked, not overwritten to expired


class TestTokenStore:
    def test_add_get_list_roundtrip(self):
        auth = _auth()
        store = TokenStore()
        token = issue_token(auth, agent_id="agent_test", principal_id="user_1", scope="groceries")
        store.add(token)
        assert store.get(token.token_id) is token
        assert token in store.list()

    def test_get_missing_token_returns_none(self):
        assert TokenStore().get("tok_does_not_exist") is None
