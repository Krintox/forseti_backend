"""
`client_signature` was a self-declared label, not a signature.

It defaulted to the literal string "ed25519_sig_valid", and all three rails
"verified" it with `if "invalid" in tx.client_signature`. A red-team vector
could only fail that check by opting in, and tampering with the amount, the
merchant, the rail or the beneficiary would not fail it at all - which is the
same defect adversarial review named in `attack_primitive_type` and
`self_approved`: the attacker decides whether it gets caught.

These tests pin the properties of the HMAC that replaced it. The important ones
are the NEGATIVE cases: a signature check that only ever passes proves nothing.
"""

import pytest

from app.models.state import PaymentRailType
from app.models.transactions import CartItem, SyntheticTransaction
from app.simulator.adapters.agentic_adapter import AgenticAp2Adapter
from app.simulator.adapters.card_adapter import CardTokenAdapter
from app.simulator.adapters.upi_adapter import UpiCircleAdapter
from app.simulator.client_signing import (
    SIGNATURE_PREFIX,
    canonical_payload,
    sign,
    tamper_after_signing,
    verify,
)


def _tx(**kw) -> SyntheticTransaction:
    base = dict(
        tx_id="t1", authority_id="a", agent_id="agent_1",
        rail=PaymentRailType.CARD_TOKEN, amount=1000.0,
        merchant_id="m", merchant_name="Kirana", merchant_mcc="5411",
        items=[CartItem(sku="SKU_GROC_01", name="Milk", category="GROCERY",
                        unit_price=1000.0, quantity=1)],
    )
    base.update(kw)
    return SyntheticTransaction(**base)


class TestAnHonestClientIsSigned:
    def test_a_new_transaction_is_signed_automatically(self):
        tx = _tx()
        assert tx.client_signature.startswith(SIGNATURE_PREFIX)
        assert verify(tx) == (True, None)

    def test_the_old_self_declared_label_no_longer_verifies(self):
        """The exact string that used to mean 'valid' must now fail."""
        ok, why = verify(_tx(client_signature="ed25519_sig_valid"))
        assert ok is False
        assert "scheme" in why

    def test_signing_is_deterministic_for_identical_terms(self):
        assert sign(_tx()) == sign(_tx())

    def test_two_agents_cannot_mint_each_others_signatures(self):
        mine, theirs = _tx(agent_id="agent_1"), _tx(agent_id="agent_2")
        assert mine.client_signature != theirs.client_signature
        borrowed = _tx(agent_id="agent_2", client_signature=mine.client_signature)
        assert verify(borrowed)[0] is False


class TestTamperingBreaksIt:
    """The whole point. Each of these was undetectable before."""

    @pytest.mark.parametrize(
        "field,value",
        [
            ("amount", 9000.0),
            ("merchant_mcc", "5734"),
            ("merchant_id", "merch_elsewhere"),
            ("rail", PaymentRailType.UPI_CIRCLE),
            ("vpa_delegate", "vpa_attacker@upi"),
            ("agent_id", "agent_someone_else"),
            ("authority_id", "auth_other"),
        ],
    )
    def test_changing_a_signed_field_invalidates_the_tag(self, field, value):
        tx = _tx()
        assert verify(tx)[0] is True
        tamper_after_signing(tx, **{field: value})
        ok, why = verify(tx)
        assert ok is False, f"tampering with {field} went undetected"
        assert "does not match" in why

    def test_swapping_the_cart_invalidates_the_tag(self):
        tx = _tx()
        tamper_after_signing(tx, items=[
            CartItem(sku="SKU_GIFT_01", name="Gift Card", category="STORED_VALUE",
                     unit_price=1000.0, quantity=1, is_stored_value=True),
        ])
        assert verify(tx)[0] is False

    def test_an_empty_or_junk_signature_fails(self):
        assert verify(_tx(client_signature=""))[0] is False
        assert verify(_tx(client_signature="not-a-signature"))[0] is False
        assert verify(_tx(client_signature=SIGNATURE_PREFIX + "00" * 32))[0] is False


class TestWhatTheSignatureDoesNotCover:
    """
    A signature over rail-decided fields would break on every authorisation, so
    the payload deliberately excludes them. Stating that explicitly beats
    letting someone discover it.
    """

    def test_rail_decisions_after_the_fact_do_not_invalidate_it(self):
        tx = _tx()
        payload_before = canonical_payload(tx)
        tx.local_rail_status = "APPROVED_LOCALLY"
        tx.local_rail_message = "fine"
        assert canonical_payload(tx) == payload_before
        assert verify(tx)[0] is True


class TestEveryRailActuallyChecks:
    @pytest.mark.parametrize(
        "adapter_cls,rail",
        [
            (CardTokenAdapter, PaymentRailType.CARD_TOKEN),
            (UpiCircleAdapter, PaymentRailType.UPI_CIRCLE),
            (AgenticAp2Adapter, PaymentRailType.AGENTIC_AP2),
        ],
    )
    def test_a_tampered_transaction_is_rejected_locally(self, adapter_cls, rail):
        # NOTE: build the cart at the right price up front. Editing
        # `tx.items[0].unit_price` after construction changes the signed payload
        # and the rail rejects it - which is the mechanism working, and was how
        # this test failed on its first run.
        tx = _tx(rail=rail, amount=500.0, items=[
            CartItem(sku="SKU_GROC_01", name="Milk", category="GROCERY",
                     unit_price=500.0, quantity=1),
        ])
        # A legitimate, signed transaction gets through the signature gate.
        approved, message = adapter_cls().validate_and_authorize_local(tx)
        assert "does not match" not in (message or "").lower(), (
            f"{adapter_cls.__name__} rejected a correctly signed transaction: {message}"
        )

        # The same transaction, amount raised in flight, must not.
        tampered = _tx(rail=rail, amount=500.0, items=[
            CartItem(sku="SKU_GROC_01", name="Milk", category="GROCERY",
                     unit_price=500.0, quantity=1),
        ])
        tamper_after_signing(tampered, amount=4500.0)
        approved2, message2 = adapter_cls().validate_and_authorize_local(tampered)
        assert approved2 is False, (
            f"{adapter_cls.__name__} AUTHORISED a transaction whose amount was raised "
            f"after signing: {message2}"
        )
        assert "signature does not match" in message2.lower(), (
            f"{adapter_cls.__name__} declined for the wrong reason: {message2}"
        )
