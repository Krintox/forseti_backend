"""
Regression tests for Batch 5: audit-key provenance, taxonomy scope honesty,
reconciliation completeness, and the advisory-AI boundary.

These pin claims that were previously true only in prose.
"""

import os

import pytest

from app.crypto.mldsa_audit import PQCDelegationAuditModule
from app.models.state import DTLGlobalAuthorityState, PaymentRailType
from app.models.transactions import CartItem, SyntheticTransaction
from app.settlement import evaluate_all as evaluate_settlement
from app.taxonomy import TAXONOMY, taxonomy_summary


def _auth() -> DTLGlobalAuthorityState:
    return DTLGlobalAuthorityState(
        authority_id="a", principal="p", agent_id="g", global_budget_ceiling=50000.0
    )


def _leg(i: int, obligation: str, action: str, rail) -> SyntheticTransaction:
    return SyntheticTransaction(
        tx_id=f"t{i}", authority_id="a", agent_id="g", rail=rail, amount=1000.0,
        merchant_id="m", merchant_name="M", merchant_mcc="5411",
        obligation_id=obligation, settlement_action=action,
        items=[CartItem(sku="SKU_GROC_01", name="x", category="GROCERY",
                        unit_price=1000.0, quantity=1)],
    )


class TestSigningKeyProvenance:
    """F-37. The signing key was derived from a seed hardcoded in the source."""

    def test_key_is_not_derivable_from_the_repository(self):
        a = PQCDelegationAuditModule()
        b = PQCDelegationAuditModule()
        if not a.available:
            pytest.skip("no genuine ML-DSA backend installed")
        assert a.pk_fingerprint != b.pk_fingerprint, (
            "two processes derived the same key - it is still deterministic from source"
        )

    def test_deterministic_key_is_opt_in_only(self, monkeypatch):
        monkeypatch.setenv("FORSETI_PQC_SEED", "reproducible-test-seed")
        a = PQCDelegationAuditModule()
        b = PQCDelegationAuditModule()
        if not a.available:
            pytest.skip("no genuine ML-DSA backend installed")
        assert a.pk_fingerprint == b.pk_fingerprint
        assert "deterministic" in a.key_provenance

    def test_status_states_the_posture_not_just_the_algorithm(self):
        status = PQCDelegationAuditModule().provider_status()
        assert status["hsm_backed"] is False
        assert "key_provenance" in status
        assert "tamper-EVIDENT" in status["security_posture"]

    def test_signed_payload_commits_to_the_exposure_breakdown(self):
        """
        Committing only to the TOTAL could not distinguish money held from
        money settled - the distinction INV_01 turns on.
        """
        mod = PQCDelegationAuditModule()
        payload = mod.build_snapshot_payload({
            "authority_id": "a", "principal": "p", "global_budget_ceiling": 10000.0,
            "total_exposure_global": 4000.0, "cumulative_spent_settled": 1000.0,
            "cumulative_spent_authorized": 2000.0, "pending_spend_global": 1000.0,
            "reserved_spend_global": 0.0, "active_policy": "STANDARD",
        }, event_root="abc123")
        breakdown = payload["exposure_breakdown"]
        assert breakdown["settled"] == 1000.0
        assert breakdown["authorized"] == 2000.0
        assert breakdown["pending"] == 1000.0
        assert payload["event_root"] == "abc123"


class TestTaxonomyScopeHonesty:
    """F-41. 46 rows were off-topic and severity was keyword-inferred."""

    def test_every_row_declares_how_its_labels_were_derived(self):
        assert all("label_provenance" in v for v in TAXONOMY)
        research = [v for v in TAXONOMY if not v["implemented"]]
        assert all("inferred from description keywords" in v["label_provenance"]
                   for v in research), "research rows must not imply researched severity"
        implemented = [v for v in TAXONOMY if v["implemented"]]
        assert all("executable implementation" in v["label_provenance"]
                   for v in implemented)

    def test_summary_reports_thesis_scope_not_just_the_headline_count(self):
        s = taxonomy_summary()
        assert s["in_thesis_scope_count"] + s["out_of_thesis_scope_count"] == s["total_vectors"]
        assert s["out_of_thesis_scope_count"] > 0, (
            "claiming every row is about delegated authority is the overclaim"
        )
        assert "not claimed as coverage" in s["scope_note"]

    def test_every_implemented_vector_is_in_thesis_scope(self):
        """What we actually built must be about the problem we claim to solve."""
        assert all(v["in_thesis_scope"] for v in TAXONOMY if v["implemented"])


class TestReconciliationCompleteness:
    """F-39. Detectors returned on the first match, hiding later conflicts."""

    def test_every_conflicting_obligation_is_reported(self):
        auth = _auth()
        card, upi = PaymentRailType.CARD_TOKEN, PaymentRailType.UPI_CIRCLE
        batch = [
            _leg(1, "ob_A", "CAPTURE", card), _leg(2, "ob_A", "REFUND", upi),
            _leg(3, "ob_B", "CAPTURE", card), _leg(4, "ob_B", "DUPLICATE_CAPTURE", card),
            _leg(5, "ob_C", "CAPTURE", upi), _leg(6, "ob_C", "REFUND", card),
        ]
        proofs = evaluate_settlement(auth, batch)
        assert len(proofs) == 3, f"only {len(proofs)} of 3 conflicts reported"
        assert {p.obligation_id for p in proofs} == {"ob_A", "ob_B", "ob_C"}

    def test_clean_batch_reports_nothing(self):
        auth = _auth()
        card = PaymentRailType.CARD_TOKEN
        clean = [_leg(1, "ob_X", "CAPTURE", card), _leg(2, "ob_Y", "CAPTURE", card)]
        assert evaluate_settlement(auth, clean) == []

    def test_module_discloses_the_idempotency_precedent(self):
        """
        Duplicate detection by shared id IS idempotency, shipped by every
        processor. The module must say so rather than implying novelty.
        """
        import app.settlement.reconciliation as recon
        doc = recon.__doc__ or ""
        assert "IDEMPOTENCY" in doc.upper()
        assert "NOT MODELLED" in doc.upper()


class TestAdvisoryAIBoundary:
    """F-40. Verify the advisory boundary is structural, not just documented."""

    def test_no_enforcement_module_imports_the_ai_package(self):
        import pathlib
        root = pathlib.Path(__file__).resolve().parents[1] / "app"
        enforcement = ["dtl", "settlement", "tokenization", "intent_firewall", "deception_lab"]
        offenders = []
        for pkg in enforcement:
            for path in (root / pkg).rglob("*.py"):
                text = path.read_text(encoding="utf-8")
                if "from ..ai" in text or "from app.ai" in text or "import ai\n" in text:
                    offenders.append(str(path))
        assert not offenders, f"enforcement modules import the AI layer: {offenders}"
