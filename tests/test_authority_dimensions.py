"""
Tests for the multidimensional delegated-authority model.

The point of these tests is the distinction that makes FORSETI defensible: an
agent can stay entirely inside the money limit and still act outside the grant.
Each test below pins one dimension and proves that violating it is caught while
the other dimensions remain satisfied - and, just as importantly, that a
transaction inside the whole grant is still allowed.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.dtl.cost_governor import AdversarialCostGovernor
from app.dtl.invariant_engine import DTLInvariantEngine
from app.dtl.ledger import DTLLedger
from app.models.state import DefensePolicy, PaymentRailType, TransactionState
from app.models.transactions import CartItem, SyntheticTransaction

AUTHORITY_ID = "auth_household_grocery_2026"


def _tx(rail=PaymentRailType.UPI_CIRCLE, amount=1000.0, mcc="5411", stored_value=False):
    return SyntheticTransaction(
        tx_id=f"tx_dim_{rail}_{amount}",
        authority_id=AUTHORITY_ID,
        agent_id="agent_test",
        rail=rail,
        amount=amount,
        merchant_id="m_test",
        merchant_name="Test Mart",
        merchant_mcc=mcc,
        items=[
            CartItem(
                sku="SKU_T",
                name="Gift Card" if stored_value else "Milk",
                category="GIFT_CARD" if stored_value else "GROCERY",
                unit_price=amount, quantity=1, is_stored_value=stored_value,
            )
        ],
    )


class TestRailDimension:
    """INV_04 - the dimension a ceiling alone cannot express."""

    def test_rail_outside_grant_is_rejected_even_with_full_headroom(self):
        ledger = DTLLedger()
        ledger.reset_authority(AUTHORITY_ID, budget=12000.0,
                               profile={"permitted_rails": [PaymentRailType.UPI_CIRCLE]})
        auth = ledger.get_authority(AUTHORITY_ID)

        # Not one rupee has been spent: the AMOUNT dimension is untouched.
        assert auth.authority_headroom == pytest.approx(12000.0)

        ok, proof = DTLInvariantEngine().evaluate_invariants(auth, _tx(PaymentRailType.CARD_TOKEN, 5000.0))
        assert ok is False
        assert proof.invariant_code == "INV_04_UNAUTHORIZED_RAIL"
        assert proof.authority_dimension == "RAIL"

    def test_permitted_rail_still_passes_under_the_same_grant(self):
        ledger = DTLLedger()
        ledger.reset_authority(AUTHORITY_ID, budget=12000.0,
                               profile={"permitted_rails": [PaymentRailType.UPI_CIRCLE]})
        auth = ledger.get_authority(AUTHORITY_ID)
        ok, proof = DTLInvariantEngine().evaluate_invariants(auth, _tx(PaymentRailType.UPI_CIRCLE, 5000.0))
        assert ok is True and proof is None

    def test_rail_containment_consumes_no_headroom(self):
        """A scope violation must not spend the user's authority to refuse it."""
        ledger = DTLLedger()
        ledger.reset_authority(AUTHORITY_ID, budget=12000.0,
                               profile={"permitted_rails": [PaymentRailType.UPI_CIRCLE]})
        auth = ledger.get_authority(AUTHORITY_ID)
        tx = _tx(PaymentRailType.CARD_TOKEN, 5000.0)
        _, proof = DTLInvariantEngine().evaluate_invariants(auth, tx)

        contained, action = AdversarialCostGovernor().apply_containment(auth, tx, proof)
        assert contained.state == TransactionState.QUARANTINED
        assert "RAIL_SCOPE_BLOCK" in action
        assert auth.authority_headroom == pytest.approx(12000.0), "refusing must not book spend"


class TestPerTransactionDimension:
    """INV_05 - bounds a single action independently of the aggregate."""

    def test_transaction_above_cap_is_caught_while_budget_is_fine(self):
        ledger = DTLLedger()
        ledger.reset_authority(AUTHORITY_ID, budget=12000.0, profile={"per_transaction_cap": 3000.0})
        auth = ledger.get_authority(AUTHORITY_ID)

        ok, proof = DTLInvariantEngine().evaluate_invariants(auth, _tx(amount=4000.0))
        assert ok is False
        assert proof.invariant_code == "INV_05_PER_TX_CAP_EXCEEDED"
        assert proof.authority_dimension == "PER_TX"
        # The aggregate ceiling was never the binding constraint.
        assert 4000.0 < auth.global_budget_ceiling

    def test_transaction_exactly_at_cap_is_allowed(self):
        ledger = DTLLedger()
        ledger.reset_authority(AUTHORITY_ID, budget=12000.0, profile={"per_transaction_cap": 3000.0})
        auth = ledger.get_authority(AUTHORITY_ID)
        ok, _ = DTLInvariantEngine().evaluate_invariants(auth, _tx(amount=3000.0))
        assert ok is True

    def test_per_tx_containment_escalates_rather_than_declines(self):
        ledger = DTLLedger()
        ledger.reset_authority(AUTHORITY_ID, budget=12000.0, profile={"per_transaction_cap": 3000.0})
        auth = ledger.get_authority(AUTHORITY_ID)
        tx = _tx(amount=4000.0)
        _, proof = DTLInvariantEngine().evaluate_invariants(auth, tx)
        _, action = AdversarialCostGovernor().apply_containment(auth, tx, proof)
        assert "STEP_UP_REQUIRED" in action
        assert auth.active_policy == DefensePolicy.STEP_UP_VERIFICATION


class TestTimeDimension:
    """INV_06 - an elapsed mandate authorises nothing, at any amount."""

    def test_expired_delegation_rejects_a_fully_in_scope_transaction(self):
        ledger = DTLLedger()
        ledger.reset_authority(AUTHORITY_ID, budget=12000.0, profile={"validity_window_hours": 0.0})
        auth = ledger.get_authority(AUTHORITY_ID)

        # Amount, rail, merchant and basket are all perfectly in scope.
        ok, proof = DTLInvariantEngine().evaluate_invariants(auth, _tx(amount=2500.0))
        assert ok is False
        assert proof.invariant_code == "INV_06_AUTHORITY_EXPIRED"
        assert proof.authority_dimension == "TIME"

    def test_live_delegation_inside_its_window_passes(self):
        ledger = DTLLedger()
        ledger.reset_authority(AUTHORITY_ID, budget=12000.0, profile={"validity_window_hours": 168.0})
        auth = ledger.get_authority(AUTHORITY_ID)
        ok, _ = DTLInvariantEngine().evaluate_invariants(auth, _tx(amount=2500.0))
        assert ok is True

    def test_expiry_is_evaluated_against_a_supplied_clock(self):
        """Time checks must be testable without waiting a week."""
        ledger = DTLLedger()
        ledger.reset_authority(AUTHORITY_ID, budget=12000.0, profile={"validity_window_hours": 24.0})
        auth = ledger.get_authority(AUTHORITY_ID)
        later = datetime.now(timezone.utc) + timedelta(hours=48)
        ok, proof = DTLInvariantEngine().evaluate_invariants(auth, _tx(amount=100.0), now=later)
        assert ok is False
        assert proof.invariant_code == "INV_06_AUTHORITY_EXPIRED"


class TestMerchantDimension:
    """INV_03 - documented from the start, but previously never enforced at runtime."""

    def test_out_of_scope_mcc_is_now_actually_rejected(self):
        auth = DTLLedger().get_authority(AUTHORITY_ID)
        assert "5311" not in auth.permitted_mccs
        ok, proof = DTLInvariantEngine().evaluate_invariants(auth, _tx(amount=8000.0, mcc="5311"))
        assert ok is False
        assert proof.invariant_code == "INV_03_UNAUTHORIZED_MCC"
        assert proof.authority_dimension == "MERCHANT"


class TestDimensionInteraction:
    def test_all_violated_dimensions_are_reported_not_just_the_first(self):
        """One transaction can break the grant in several ways at once."""
        ledger = DTLLedger()
        ledger.reset_authority(AUTHORITY_ID, budget=5000.0, profile={
            "permitted_rails": [PaymentRailType.UPI_CIRCLE],
            "per_transaction_cap": 1000.0,
        })
        auth = ledger.get_authority(AUTHORITY_ID)
        # Wrong rail, over the per-tx cap, out-of-scope MCC, stored value, over budget.
        tx = _tx(PaymentRailType.CARD_TOKEN, amount=9000.0, mcc="5311", stored_value=True)

        proofs = DTLInvariantEngine().evaluate_all(auth, tx)
        codes = {p.invariant_code for p in proofs}
        assert "INV_04_UNAUTHORIZED_RAIL" in codes
        assert "INV_05_PER_TX_CAP_EXCEEDED" in codes
        assert "INV_03_UNAUTHORIZED_MCC" in codes
        assert "INV_02_SEMANTIC_INTENT_DRIFT" in codes
        assert "INV_01_GLOBAL_BUDGET_EXCEEDED" in codes

    def test_registry_covers_every_dimension_exactly_once(self):
        """
        Scoped to `kind == "authority_dimension"`. INV_08_MANDATE_SUSPENDED is
        also in the registry but is a POLICY STATE, not a dimension - it exists
        so the Blue escalation ladder is enforced rather than displayed, and
        counting it here would double up on TIME.
        """
        rows = DTLInvariantEngine.registry()
        dimension_rows = [r for r in rows if r.get("kind") == "authority_dimension"]
        dims = [r["dimension"] for r in dimension_rows]
        assert sorted(dims) == sorted(
            ["TIME", "RAIL", "PER_TX", "MERCHANT", "PURPOSE", "AMOUNT", "BENEFICIARY"]
        )
        assert len(rows) == len(set(r["code"] for r in rows))
        # Every row must declare which kind it is, so the UI cannot present a
        # policy state as an eighth authority dimension.
        assert all(r.get("kind") in {"authority_dimension", "policy_state"} for r in rows)
        assert any(r["code"] == "INV_08_MANDATE_SUSPENDED" and r["kind"] == "policy_state"
                   for r in rows)

    def test_default_grant_is_unconstrained_on_the_new_dimensions(self):
        """
        Back-compat: the historical demo (₹10k, any rail, no per-tx cap) must
        behave exactly as before, or every existing result silently changes.
        """
        auth = DTLLedger().get_authority(AUTHORITY_ID)
        assert auth.per_transaction_cap is None
        assert len(auth.permitted_rail_values) == 3
        assert auth.is_expired() is False
        ok, _ = DTLInvariantEngine().evaluate_invariants(auth, _tx(PaymentRailType.CARD_TOKEN, 4000.0))
        assert ok is True


class TestAuthorityVector:
    def test_vector_exposes_one_row_per_dimension_with_its_invariant(self):
        auth = DTLLedger().get_authority(AUTHORITY_ID)
        vector = auth.authority_vector()
        assert set(vector) == {"AMOUNT", "PER_TX", "RAIL", "MERCHANT", "PURPOSE", "TIME", "BENEFICIARY"}
        assert vector["AMOUNT"]["invariant"] == "INV_01_GLOBAL_BUDGET_EXCEEDED"
        assert vector["RAIL"]["invariant"] == "INV_04_UNAUTHORIZED_RAIL"
        assert vector["TIME"]["expired"] is False

    def test_vector_tracks_scope_changes(self):
        ledger = DTLLedger()
        ledger.update_authority_scope(AUTHORITY_ID, {"permitted_rails": [PaymentRailType.UPI_CIRCLE]})
        vector = ledger.get_authority(AUTHORITY_ID).authority_vector()
        assert vector["RAIL"]["granted"] == ["UPI_CIRCLE"]
        assert vector["RAIL"]["unconstrained"] is False


class TestRedAgentKnowsAllVectors:
    """
    The closed-loop Red agent picks its next move by argmax over
    feedback.adaptive_planner.STRATEGY_PROFILE. If a strategy is missing from
    that table it can never be recommended by the adaptation engine, even
    though the orchestrator can run it - exactly the gap that let three
    authority-dimension vectors (rail, per-tx, time) ship without the "closed
    loop" ever being able to choose them.
    """

    def test_every_orchestrator_strategy_has_a_planner_profile(self):
        from app.arena.orchestrator import STRATEGY_BY_ROUND
        from app.feedback.adaptive_planner import STRATEGY_PROFILE

        for strategy in STRATEGY_BY_ROUND.values():
            assert strategy in STRATEGY_PROFILE, (
                f"{strategy} is runnable via the orchestrator but the adaptive "
                f"planner can never select it as a next move"
            )

    def test_new_dimension_vectors_are_scoreable(self):
        from app.feedback.adaptive_planner import AdaptiveRedPlanner
        from app.feedback.attack_memory import AttackMemoryStore

        planner = AdaptiveRedPlanner(AttackMemoryStore())
        table = planner.score_strategies(headroom_ratio=1.0)
        scored = {row["strategy"] for row in table}
        assert {"RAIL_SCOPE_VIOLATION", "PER_TX_BREACH", "LAPSED_MANDATE"} <= scored
        for row in table:
            if row["strategy"] in ("RAIL_SCOPE_VIOLATION", "PER_TX_BREACH", "LAPSED_MANDATE"):
                assert row["score"] > 0.0
                assert row["expected_defence"].startswith("INV_0")


class TestCounterfactualIsolation:
    """
    "What if the limit had been X?" is an OBSERVATION. Running those
    hypotheticals against the live orchestrator previously reset the authority
    between each simulated ceiling, which wiped the operator's rail scope and
    per-transaction cap, erased the event log, and cleared last_round - so
    Incident Report / Policy Advisor / Customer Notice all began reporting
    "no round has been executed yet" right after a counterfactual ran.
    """

    def test_sandbox_shares_the_model_but_not_the_mutable_state(self):
        from app.arena.orchestrator import ArenaBattleOrchestrator

        live = ArenaBattleOrchestrator()
        sandbox = live.sandbox()

        # The expensive part - the loaded model and its explainer - is shared,
        # so a what-if does not pay a model reload.
        assert sandbox.detector.model is live.detector.model
        assert sandbox.detector.raw_model is live.detector.raw_model
        assert sandbox.pqc_module is live.pqc_module

        # But the detector is NO LONGER stateless: it carries per-authority
        # history and a live entity graph so that serving features match
        # training features. Sharing the whole detector would let a
        # hypothetical round write velocity/graph state into the operator's
        # real session, which is the same class of bug as sharing the ledger.
        assert sandbox.detector is not live.detector
        assert sandbox.detector._histories is not live.detector._histories
        assert sandbox.detector._graph is not live.detector._graph

        # Everything carrying round state must be independent.
        assert sandbox.ledger is not live.ledger
        assert sandbox.recorder is not live.recorder
        assert sandbox.simulator is not live.simulator
        assert sandbox.feedback_engine is not live.feedback_engine

    def test_hypothetical_scoring_does_not_pollute_live_serving_context(self):
        """
        The behavioural version of the test above: running transactions through
        the sandbox detector must leave the live detector's serving context
        empty, or counterfactuals would silently inflate the real session's
        velocity and graph features.
        """
        from app.arena.orchestrator import ArenaBattleOrchestrator
        from app.models.state import DTLGlobalAuthorityState, PaymentRailType
        from app.models.transactions import CartItem, SyntheticTransaction

        live = ArenaBattleOrchestrator()
        sandbox = live.sandbox()
        auth = DTLGlobalAuthorityState(
            authority_id="auth_x", principal="p", agent_id="agt_x",
            global_budget_ceiling=10000.0,
        )
        for i in range(5):
            tx = SyntheticTransaction(
                tx_id=f"tx_hypo_{i}", authority_id="auth_x", agent_id="agt_x",
                rail=PaymentRailType.UPI_CIRCLE, amount=500.0,
                merchant_id=f"merch_{i}", merchant_name="M", merchant_mcc="5411",
                items=[CartItem(sku="s", name="n", category="GROCERY",
                                unit_price=500.0, quantity=1)],
            )
            sandbox.detector.observe(auth, tx)

        assert sandbox.detector.context_status()["transactions_in_history"] == 5
        assert live.detector.context_status()["transactions_in_history"] == 0
        assert live.detector.context_status()["graph_nodes"] == 0

    def test_running_a_hypothetical_leaves_live_state_untouched(self):
        import asyncio

        from app.arena.orchestrator import AUTHORITY_ID, ArenaBattleOrchestrator
        from app.models.state import PaymentRailType

        live = ArenaBattleOrchestrator()
        live.reset(12000.0)
        # Go through the operator-facing setter (what POST /api/arena/
        # authority-scope calls), so the choice is recorded as the operator's
        # baseline grant rather than a transient ledger mutation.
        live.set_authority_scope({
            "permitted_rails": [PaymentRailType.UPI_CIRCLE],
            "per_transaction_cap": 3000.0,
        })
        asyncio.run(live.run_round_stream(
            round_number=2, dtl_enabled=True, event_callback=None,
            speed=100.0, strategy_override="CROSS_RAIL_SPLIT"))

        events_before = len(live.recorder.events)
        assert events_before > 0 and live.last_round is not None

        # A hypothetical at a different ceiling.
        sandbox = live.sandbox()
        sandbox.reset(5000.0)
        asyncio.run(sandbox.run_round_stream(
            round_number=2, dtl_enabled=True, event_callback=None,
            speed=100.0, strategy_override="CROSS_RAIL_SPLIT"))

        auth = live.ledger.get_authority(AUTHORITY_ID)
        assert auth.permitted_rail_values == ["UPI_CIRCLE"], "rail scope must survive a what-if"
        assert auth.per_transaction_cap == 3000.0, "per-tx cap must survive a what-if"
        assert auth.global_budget_ceiling == 12000.0, "live ceiling must survive a what-if"
        assert len(live.recorder.events) == events_before, "event log must survive a what-if"
        assert live.last_round is not None, "last_round must survive a what-if"


class TestEveryVectorIsActuallyRunnable:
    """
    Three of the original six vectors (BASELINE_POISONING, REVOCATION_FLOOD,
    VELOCITY_BURST) returned HTTP 500 for the entire life of the project: their
    generators return a LIST of transactions, but the selector wrapped them in
    another list, so the round crashed on `t.amount` with "'list' object has no
    attribute 'amount'". Selecting "all vectors" in the UI hit it immediately.
    """

    def test_every_strategy_yields_a_flat_list_of_transactions(self):
        from app.arena.orchestrator import STRATEGY_BY_ROUND, ArenaBattleOrchestrator
        from app.models.transactions import SyntheticTransaction

        orch = ArenaBattleOrchestrator()
        for strategy in STRATEGY_BY_ROUND.values():
            txs = orch._select_attack(strategy)
            assert isinstance(txs, list) and txs, f"{strategy} produced nothing"
            assert all(isinstance(t, SyntheticTransaction) for t in txs), \
                f"{strategy} produced a nested/!SyntheticTransaction payload"
            # The exact expression that used to crash the round.
            assert sum(t.amount for t in txs) > 0

    def test_every_strategy_completes_a_round(self):
        import asyncio

        from app.arena.orchestrator import STRATEGY_BY_ROUND, ArenaBattleOrchestrator

        orch = ArenaBattleOrchestrator()
        for round_number, strategy in STRATEGY_BY_ROUND.items():
            orch.reset(10000.0)
            result = asyncio.run(orch.run_round_stream(
                round_number=round_number, dtl_enabled=True, event_callback=None,
                speed=100.0, strategy_override=strategy))
            assert result["winner"] in ("RED", "BLUE", "NONE"), strategy
            assert result["step_results"], f"{strategy} produced no steps"


class TestVectorProfilesDoNotContaminateLaterRounds:
    """
    A vector that re-grants the authority to demonstrate its dimension must not
    leave that grant in force for the next vector. Running the full campaign
    previously reported INV_05_PER_TX_CAP_EXCEEDED for Intent Laundering and
    Scope Creep - the per-transaction cap from PER_TX_BREACH was still applied -
    so those vectors demonstrated the wrong invariant entirely.
    """

    EXPECTED_DIMENSION = {
        "CROSS_RAIL_SPLIT": "AMOUNT",
        "RAIL_SCOPE_VIOLATION": "RAIL",
        "PER_TX_BREACH": "PER_TX",
        "INTENT_LAUNDERING": "PURPOSE",
        "LAPSED_MANDATE": "TIME",
    }

    #: SCOPE_CREEP is deliberately NOT in the table above.
    #:
    #: It used to be, expecting MERCHANT - and it passed for the wrong reason.
    #: Its out-of-scope leg used MCC 5311, which the PARENT grant also refuses,
    #: so INV_03 fired at the parent level and the sub-delegation proved
    #: nothing. (The vector's own comment claimed "the parent grant permits
    #: 5311"; it does not.) The leg now uses 5499 - genuinely permitted to the
    #: parent, genuinely denied to the sub-agent's link - so the ONLY thing
    #: that refuses it is the chain, which is the entire point of the vector.
    #:
    #: Its assertion is therefore about CHAIN_* codes, below.
    EXPECTED_CHAIN_CODES = {"CHAIN_MERCHANT_OUT_OF_SCOPE", "CHAIN_PER_TX_EXCEEDED"}

    def test_full_campaign_demonstrates_each_dimension_in_turn(self):
        import asyncio

        from app.arena.orchestrator import ArenaBattleOrchestrator

        orch = ArenaBattleOrchestrator()
        orch.reset(10000.0)
        # Deliberately ordered so the profile-bearing vectors run BEFORE the
        # ones that rely on the operator's own grant.
        order = ["CROSS_RAIL_SPLIT", "RAIL_SCOPE_VIOLATION", "PER_TX_BREACH",
                 "INTENT_LAUNDERING", "SCOPE_CREEP", "LAPSED_MANDATE"]
        for strategy in order:
            result = asyncio.run(orch.run_round_stream(
                round_number=2, dtl_enabled=True, event_callback=None,
                speed=100.0, strategy_override=strategy))
            if strategy == "SCOPE_CREEP":
                # Caught by the delegation CHAIN, never by a parent invariant.
                #
                # Running 5th, the grant may be fully consumed - four vectors
                # spent it - and then issuing a sub-delegation pool is correctly
                # refused, because a parent cannot lend authority it does not
                # hold. Both endings are legitimate. What is NOT legitimate is
                # the third one this used to do: issuance fails, no link exists,
                # `evaluate_action` allows everything because an agent with no
                # link acts under the root authority, and the vector silently
                # tests nothing while reporting a clean round.
                codes = {c.get("code") for c in (result.get("chain_violations") or [])}
                refusals = [e for e in result.get("events", [])
                            if "SUB-DELEGATION REFUSED" in (e.get("arrow_label") or "")]
                assert (codes & self.EXPECTED_CHAIN_CODES) or refusals, (
                    "SCOPE_CREEP neither exercised the chain nor stated why it could not - "
                    "it silently stopped testing its own mechanism"
                )
                parent_dims = {s["proof"]["authority_dimension"]
                               for s in result["step_results"] if s.get("proof")}
                assert "MERCHANT" not in parent_dims, (
                    "the parent grant refused the merchant too, so this round does not "
                    "demonstrate sub-delegation scope at all"
                )
                continue

            proofs = [s["proof"] for s in result["step_results"] if s.get("proof")]
            assert proofs, f"{strategy} should violate its dimension"
            # Check the dimension appears ANYWHERE in the round, not only on
            # the first violating step. Several vectors now open with a
            # deliberately legitimate warm-up leg (a real adversary establishes
            # a plausible pattern before the boundary test), and in a campaign
            # where exposure has accumulated that leg can trip AMOUNT first.
            # Requiring proofs[0] made the assertion about leg ordering rather
            # than about profile contamination, which is what it exists to catch.
            dimensions = {p["authority_dimension"] for p in proofs}
            assert self.EXPECTED_DIMENSION[strategy] in dimensions, (
                f"{strategy} reported {sorted(dimensions)} but never "
                f"{self.EXPECTED_DIMENSION[strategy]} - a previous vector's profile is "
                f"still in force"
            )

    def test_an_optional_dimension_can_be_cleared_back_to_unconstrained(self):
        from app.dtl.ledger import DTLLedger

        ledger = DTLLedger()
        ledger.update_authority_scope(AUTHORITY_ID, {"per_transaction_cap": 3000.0})
        assert ledger.get_authority(AUTHORITY_ID).per_transaction_cap == 3000.0

        # Without allow_none the None is treated as "leave alone", so the cap
        # could never be lifted again.
        ledger.update_authority_scope(AUTHORITY_ID, {"per_transaction_cap": None})
        assert ledger.get_authority(AUTHORITY_ID).per_transaction_cap == 3000.0

        ledger.update_authority_scope(AUTHORITY_ID, {"per_transaction_cap": None}, allow_none=True)
        assert ledger.get_authority(AUTHORITY_ID).per_transaction_cap is None

    def test_operator_scope_survives_a_profile_bearing_vector(self):
        """An operator who set "UPI only" must get it back after a vector round."""
        import asyncio

        from app.arena.orchestrator import ArenaBattleOrchestrator
        from app.models.state import PaymentRailType

        orch = ArenaBattleOrchestrator()
        orch.reset(12000.0)
        orch.set_authority_scope({"permitted_rails": [PaymentRailType.UPI_CIRCLE]})

        # PER_TX_BREACH re-grants with all rails + a cap.
        asyncio.run(orch.run_round_stream(
            round_number=8, dtl_enabled=True, event_callback=None,
            speed=100.0, strategy_override="PER_TX_BREACH"))
        # A vector with no profile must restore the operator's UPI-only grant.
        asyncio.run(orch.run_round_stream(
            round_number=1, dtl_enabled=True, event_callback=None,
            speed=100.0, strategy_override="INTENT_LAUNDERING"))

        auth = orch.ledger.get_authority(AUTHORITY_ID)
        assert auth.permitted_rail_values == ["UPI_CIRCLE"]
        assert auth.per_transaction_cap is None
        assert auth.global_budget_ceiling == 12000.0


class TestScopeCreepExercisesTheChainOnAFreshGrant:
    """
    The campaign test above accepts a refusal, because by round five the grant
    is genuinely exhausted. On a fresh grant there is no such excuse: the chain
    must be what refuses the sub-agent, and the parent must NOT.
    """

    def test_the_chain_refuses_and_the_parent_does_not(self):
        import asyncio

        from app.arena.orchestrator import ArenaBattleOrchestrator

        orch = ArenaBattleOrchestrator()
        orch.reset(12000.0)
        result = asyncio.run(orch.run_round_stream(
            round_number=2, dtl_enabled=True, speed=100.0,
            strategy_override="SCOPE_CREEP"))

        codes = {c.get("code") for c in (result.get("chain_violations") or [])}
        assert "CHAIN_MERCHANT_OUT_OF_SCOPE" in codes, (
            f"expected the sub-delegation to refuse the merchant; got {sorted(codes)}"
        )

        parent_dims = {s["proof"]["authority_dimension"]
                       for s in result["step_results"] if s.get("proof")}
        assert "MERCHANT" not in parent_dims, (
            "the PARENT grant also refused the merchant - the leg must use an MCC the "
            "parent permits, or the vector proves nothing about sub-delegation"
        )

    def test_a_real_pool_is_carved_from_the_parent(self):
        import asyncio

        from app.arena.orchestrator import ArenaBattleOrchestrator

        orch = ArenaBattleOrchestrator()
        orch.reset(12000.0)
        result = asyncio.run(orch.run_round_stream(
            round_number=2, dtl_enabled=True, speed=100.0,
            strategy_override="SCOPE_CREEP"))
        issued = [e for e in result.get("events", [])
                  if "SUB-DELEGATION" in (e.get("arrow_label") or "")
                  and "REFUSED" not in (e.get("arrow_label") or "")]
        assert issued, "no sub-delegation link was issued on a fresh grant"
        assert issued[0]["payload"]["reserved_spend_global"] > 0, (
            "reserved_spend_global stayed zero - the pool was not really carved"
        )
