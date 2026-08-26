"""
Regression tests for the two measured defects that invalidated FORSETI's
headline scientific claim, plus the gates added to stop them recurring.

Both defects were invisible in the headline metrics and were caught only by
running the code rather than reading it, which is exactly why they get pinned
here:

  1. CATEGORICAL LEAKAGE - four of six attack families carried an MCC that
     never occurred in legitimate traffic, so held-out families were separable
     by one categorical value. Held-out REVOCATION_FLOOD scored PR-AUC 1.000
     with mean predicted probability exactly 1.0, reported as generalisation.

  2. TRAIN/SERVE SKEW - the serving path called extract_features() with no
     history and no graph context, so 23 of 37 features were exactly 0.0 at
     inference while training populated all of them. The live "ML RISK %" was
     produced by a model operating outside its training distribution.
"""

import pandas as pd
import pytest

from app.detector.dataset_builder import SyntheticMLDatasetBuilder
from app.detector.feature_schema import ALL_FEATURE_NAMES
from app.detector.inference import HybridMLDetectorInference
from app.detector.leakage_audit import audit_categorical_leakage, family_separability
from app.models.state import DTLGlobalAuthorityState, PaymentRailType
from app.models.transactions import CartItem, SyntheticTransaction


@pytest.fixture(scope="module")
def dataset() -> pd.DataFrame:
    """Same parameters the training pipeline actually uses."""
    return SyntheticMLDatasetBuilder(seed=42).generate_trajectory(
        num_samples=6000, fraud_ratio=0.012
    )


def _tx(i: int, merchant_id: str, mcc: str, amount: float, rail) -> SyntheticTransaction:
    return SyntheticTransaction(
        tx_id=f"tx_{i}", authority_id="auth_t", agent_id=f"agt_{i % 4}",
        rail=rail, amount=amount, merchant_id=merchant_id,
        merchant_name="M", merchant_mcc=mcc, device_id=f"dev_{i % 3}",
        items=[CartItem(sku="s", name="n", category="GROCERY",
                        unit_price=amount, quantity=1)],
    )


class TestCategoricalLeakage:
    def test_no_categorical_value_determines_the_label(self, dataset):
        report = audit_categorical_leakage(dataset)
        assert report["passed"], (
            f"categorical leakage detected in {report['leaking_fields']}: "
            f"{ {f: report['per_field'][f]['leaking_values'] for f in report['leaking_fields']} }"
        )

    def test_no_attack_family_is_identifiable_by_one_categorical_value(self, dataset):
        """
        The specific defect: merchant_mcc=5734 identified REVOCATION_FLOOD with
        precision 1.0 AND recall 1.0. A family that separable makes any
        holdout claim about it meaningless.
        """
        for family, best in family_separability(dataset).items():
            shortcut_strength = min(best["precision"], best["recall"])
            assert shortcut_strength < 0.5, (
                f"{family} is identifiable by {best['field']}={best['value']} "
                f"(precision={best['precision']}, recall={best['recall']}). "
                f"Holding this family out would measure the shortcut, not generalisation."
            )

    def test_attack_mccs_also_occur_in_legitimate_traffic(self, dataset):
        """No MCC may be attack-exclusive - that was the original mechanism."""
        attack_mccs = set(dataset[dataset.is_fraud == 1]["merchant_mcc"])
        legit_mccs = set(dataset[dataset.is_fraud == 0]["merchant_mcc"])
        exclusive = attack_mccs - legit_mccs
        assert not exclusive, f"MCC(s) {exclusive} appear only in attack rows"

    def test_legitimate_traffic_reaches_out_of_scope_merchants(self, dataset):
        """
        A real household sometimes shops outside its delegation. If it never
        did, "MCC out of permitted set" would again mean "fraud".
        """
        in_scope = SyntheticMLDatasetBuilder.IN_SCOPE_MCCS
        legit = dataset[dataset.is_fraud == 0]
        out_of_scope_rate = (~legit["merchant_mcc"].isin(in_scope)).mean()
        assert 0.02 < out_of_scope_rate < 0.20, (
            f"legitimate out-of-scope rate {out_of_scope_rate:.3f} outside expected band"
        )

    def test_no_merchant_belongs_to_exactly_one_attack_family(self, dataset):
        """Every family used to own a dedicated merchant node, which PageRank read."""
        attacks = dataset[(dataset.is_fraud == 1) & (dataset.attack_family != "NONE")]
        per_merchant_families = attacks.groupby("merchant_id")["attack_family"].nunique()
        busy = per_merchant_families[
            attacks.groupby("merchant_id").size() >= 20
        ]
        assert (busy > 1).any(), "no merchant is shared across attack families"

    def test_audit_actually_detects_a_known_leak(self):
        """
        Guards the guard. An audit that cannot catch the original bug is
        worthless, so reconstruct that layout and assert it fails.
        """
        rows = [{"is_fraud": 0, "attack_family": "NONE", "merchant_mcc": "5411",
                 "merchant_id": "m_legit", "device_id": "d", "rail": "CARD_TOKEN"}
                for _ in range(1000)]
        rows += [{"is_fraud": 1, "attack_family": "REVOCATION_FLOOD", "merchant_mcc": "5734",
                  "merchant_id": "m_revoc", "device_id": "d", "rail": "CARD_TOKEN"}
                 for _ in range(80)]
        leaky = pd.DataFrame(rows)

        report = audit_categorical_leakage(leaky)
        assert not report["passed"]
        assert "merchant_mcc" in report["leaking_fields"]

        best = family_separability(leaky)["REVOCATION_FLOOD"]
        assert best["precision"] == 1.0 and best["recall"] == 1.0


class TestTrainServeSkew:
    def test_serving_context_populates_windowed_and_graph_features(self):
        det = HybridMLDetectorInference(eager_explainer=False)
        auth = DTLGlobalAuthorityState(
            authority_id="auth_t", principal="p", agent_id="agt_0",
            global_budget_ceiling=100000.0,
        )
        rails = [PaymentRailType.CARD_TOKEN, PaymentRailType.UPI_CIRCLE,
                 PaymentRailType.AGENTIC_AP2]
        mccs = ["5411", "5812", "5311", "5045"]

        _, cold = det.score(auth, _tx(0, "merch_0", "5411", 900.0, rails[0]))
        cold_zeros = sum(1 for n in ALL_FEATURE_NAMES if float(cold.get(n, 0.0)) == 0.0)

        for i in range(1, 25):
            tx = _tx(i, f"merch_{i % 6}", mccs[i % 4], 400.0 + i * 50, rails[i % 3])
            det.score(auth, tx)
            det.observe(auth, tx)
            auth.cumulative_spent_authorized += tx.amount
        det.refresh_graph_metrics()

        _, warm = det.score(auth, _tx(99, "merch_2", "5411", 2200.0, rails[1]))
        warm_zeros = sum(1 for n in ALL_FEATURE_NAMES if float(warm.get(n, 0.0)) == 0.0)

        assert warm_zeros < cold_zeros, "serving context populated nothing"
        assert warm_zeros <= 10, (
            f"{warm_zeros}/37 features still zero with a warm context - "
            f"train/serve skew persists"
        )

    def test_graph_features_are_live_at_serving_time(self):
        """
        graph_merchant_pagerank was a top SHAP driver pinned to exactly 0.0 in
        every live round, because PaymentGraph's 200-transaction refresh cadence
        never fires in a session-sized graph.
        """
        det = HybridMLDetectorInference(eager_explainer=False)
        auth = DTLGlobalAuthorityState(
            authority_id="auth_t", principal="p", agent_id="agt_0",
            global_budget_ceiling=100000.0,
        )
        rails = [PaymentRailType.CARD_TOKEN, PaymentRailType.UPI_CIRCLE]
        for i in range(1, 20):
            tx = _tx(i, f"merch_{i % 5}", "5411", 600.0, rails[i % 2])
            det.score(auth, tx)
            det.observe(auth, tx)
        det.refresh_graph_metrics()

        _, feats = det.score(auth, _tx(99, "merch_1", "5411", 800.0, rails[0]))
        assert feats["graph_merchant_pagerank"] > 0.0
        assert feats["graph_agent_pagerank"] > 0.0
        assert feats["graph_agent_out_degree"] > 0.0

    def test_observe_does_not_leak_a_transaction_into_its_own_features(self):
        """Snapshot-before-add: the same discipline the training loop uses."""
        det = HybridMLDetectorInference(eager_explainer=False)
        auth = DTLGlobalAuthorityState(
            authority_id="auth_t", principal="p", agent_id="agt_solo",
            global_budget_ceiling=50000.0,
        )
        tx = _tx(1, "merch_unique", "5411", 700.0, PaymentRailType.UPI_CIRCLE)
        _, before = det.score(auth, tx)
        assert before["graph_merchant_in_degree"] == 0.0, (
            "transaction saw its own edge in its own features"
        )
        det.observe(auth, tx)
        _, after = det.score(auth, tx)
        assert after["graph_merchant_in_degree"] > 0.0

    def test_reset_context_clears_serving_state(self):
        det = HybridMLDetectorInference(eager_explainer=False)
        auth = DTLGlobalAuthorityState(
            authority_id="auth_t", principal="p", agent_id="agt_0",
            global_budget_ceiling=50000.0,
        )
        for i in range(4):
            det.observe(auth, _tx(i, "merch_a", "5411", 300.0, PaymentRailType.CARD_TOKEN))
        assert det.context_status()["transactions_in_history"] == 4
        det.reset_context()
        assert det.context_status()["transactions_in_history"] == 0
        assert det.context_status()["graph_nodes"] == 0
