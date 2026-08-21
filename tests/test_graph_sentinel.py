"""
Tests for Module 4 of the Agentic Payment Security Runtime expansion:
the Payment Graph Sentinel (cross-authority entity graph + graph features).
"""

import pytest

from app.detector.ablation import DTL_ONLY_GROUPS, build_variants
from app.detector.dataset_builder import SyntheticMLDatasetBuilder
from app.detector.feature_schema import ALL_FEATURE_NAMES, FEATURE_GROUPS, DTLFeatureExtractor
from app.dtl.ledger import DTLLedger
from app.graph_sentinel import GRAPH_FEATURE_NAMES, PaymentGraph
from app.models.state import PaymentRailType
from app.models.transactions import CartItem, SyntheticTransaction

AUTHORITY_ID = "auth_household_grocery_2026"


class TestPaymentGraph:
    def test_snapshot_before_any_transaction_is_zero(self):
        g = PaymentGraph()
        feats = g.snapshot_features("agent_a", "merchant_x", "dev_1")
        assert feats["graph_agent_out_degree"] == 0.0
        assert feats["graph_merchant_in_degree"] == 0.0
        assert feats["graph_device_shared_count"] == 0.0

    def test_add_transaction_increments_degree_for_the_next_snapshot(self):
        g = PaymentGraph()
        g.add_transaction("agent_a", "merchant_x", "dev_1")
        feats = g.snapshot_features("agent_a", "merchant_x", "dev_1")
        assert feats["graph_agent_out_degree"] == 1.0
        assert feats["graph_merchant_in_degree"] == 1.0

    def test_snapshot_never_includes_the_transaction_being_evaluated(self):
        """The core non-leakage property: snapshot BEFORE add reflects prior state only."""
        g = PaymentGraph()
        g.add_transaction("agent_a", "merchant_x", None)
        before = g.snapshot_features("agent_b", "merchant_x", None)
        # agent_b has never transacted; merchant_x has exactly ONE prior edge
        # (from agent_a), so agent_b's own upcoming transaction is not yet
        # reflected in merchant_x's in-degree.
        assert before["graph_agent_out_degree"] == 0.0
        assert before["graph_merchant_in_degree"] == 1.0

    def test_shared_device_count_reflects_distinct_agents(self):
        g = PaymentGraph()
        g.add_transaction("agent_a", "merchant_x", "dev_shared")
        g.add_transaction("agent_b", "merchant_y", "dev_shared")
        feats = g.snapshot_features("agent_c", "merchant_z", "dev_shared")
        assert feats["graph_device_shared_count"] == 2.0  # agent_a and agent_b

    def test_unshared_device_reports_zero(self):
        g = PaymentGraph()
        g.add_transaction("agent_a", "merchant_x", "dev_only_a")
        feats = g.snapshot_features("agent_b", "merchant_y", "dev_unique_b")
        assert feats["graph_device_shared_count"] == 0.0

    def test_refresh_populates_pagerank_and_community(self):
        g = PaymentGraph()
        for i in range(10):
            g.add_transaction(f"agent_{i % 3}", f"merchant_{i % 2}", None)
        g.refresh_global_metrics()
        feats = g.snapshot_features("agent_0", "merchant_0", None)
        assert feats["graph_agent_pagerank"] > 0.0
        assert feats["graph_merchant_pagerank"] > 0.0
        assert 0.0 <= feats["graph_community_size_ratio"] <= 1.0

    def test_stats_reports_real_counts(self):
        g = PaymentGraph()
        g.add_transaction("agent_a", "merchant_x", "dev_1")
        g.add_transaction("agent_a", "merchant_y", "dev_1")
        stats = g.stats()
        assert stats["nodes"] == 3  # agent_a, merchant_x, merchant_y
        assert stats["edges"] == 2
        assert stats["transactions_ingested"] == 2
        assert stats["distinct_devices"] == 1


class TestFeatureSchemaIntegration:
    def _tx(self):
        return SyntheticTransaction(
            tx_id="tx_graph_test", authority_id=AUTHORITY_ID, agent_id="agent_test",
            rail=PaymentRailType.UPI_CIRCLE, amount=1000.0, merchant_id="m_test",
            merchant_name="Test Mart", merchant_mcc="5411",
            items=[CartItem(sku="SKU_T", name="Milk", category="GROCERY",
                             unit_price=1000.0, quantity=1)],
        )

    def test_graph_group_registered_in_all_feature_names(self):
        assert "graph" in FEATURE_GROUPS
        for name in GRAPH_FEATURE_NAMES:
            assert name in ALL_FEATURE_NAMES
        assert "graph_cross_rail_fanout_velocity" in ALL_FEATURE_NAMES

    def test_missing_graph_features_default_to_zero_not_fabricated(self):
        """The live-arena path: no graph context passed at all."""
        auth = DTLLedger().get_authority(AUTHORITY_ID)
        feats = DTLFeatureExtractor.extract_features(auth, self._tx())
        assert feats["graph_agent_out_degree"] == 0.0
        assert feats["graph_agent_pagerank"] == 0.0
        assert feats["graph_device_shared_count"] == 0.0
        # This one IS computed without a graph, from tx_history alone.
        assert feats["graph_cross_rail_fanout_velocity"] == 1.0  # single tx, single rail

    def test_provided_graph_features_pass_through(self):
        auth = DTLLedger().get_authority(AUTHORITY_ID)
        provided = {"graph_agent_out_degree": 4.0, "graph_agent_pagerank": 0.12}
        feats = DTLFeatureExtractor.extract_features(auth, self._tx(), graph_features=provided)
        assert feats["graph_agent_out_degree"] == 4.0
        assert feats["graph_agent_pagerank"] == 0.12
        # Un-provided graph features still default sanely.
        assert feats["graph_merchant_in_degree"] == 0.0

    def test_cross_rail_fanout_velocity_reflects_trailing_history(self):
        auth = DTLLedger().get_authority(AUTHORITY_ID)
        history = [
            {"rail": "CARD_TOKEN", "amount": 500.0, "mcc": "5411",
             "timestamp": "2026-05-01T08:00:00"},
            {"rail": "UPI_CIRCLE", "amount": 500.0, "mcc": "5411",
             "timestamp": "2026-05-01T08:01:00"},
        ]
        feats = DTLFeatureExtractor.extract_features(auth, self._tx(), tx_history=history)
        # 3 distinct rails (CARD_TOKEN, UPI_CIRCLE from history + UPI_CIRCLE of
        # this tx, which de-dupes to 2) across a window of 3 -> 2/3.
        assert feats["graph_cross_rail_fanout_velocity"] == pytest.approx(2.0 / 3.0)


class TestDatasetBuilderIntegration:
    def test_generated_dataset_has_graph_columns_with_real_variation(self):
        builder = SyntheticMLDatasetBuilder(seed=7)
        df = builder.generate_trajectory(num_samples=400, fraud_ratio=0.05)
        for name in GRAPH_FEATURE_NAMES:
            assert name in df.columns
        # With 400 rows across 25 authorities and a handful of merchants,
        # SOME nonzero graph signal must appear - a builder that always wrote
        # zeros would pass a "column exists" check but be worthless.
        assert (df["graph_agent_out_degree"] > 0).sum() > 0
        assert (df["graph_merchant_in_degree"] > 0).sum() > 0

    def test_builder_exposes_the_final_graph_for_introspection(self):
        builder = SyntheticMLDatasetBuilder(seed=7)
        builder.generate_trajectory(num_samples=300, fraud_ratio=0.05)
        assert builder.last_graph is not None
        stats = builder.last_graph.stats()
        assert stats["nodes"] > 0
        assert stats["edges"] > 0
        assert stats["transactions_ingested"] == 300

    def test_device_ring_pool_actually_gets_used(self):
        """Shared-device assignment is probabilistic; over 400 rows it must fire."""
        builder = SyntheticMLDatasetBuilder(seed=7)
        builder.generate_trajectory(num_samples=400, fraud_ratio=0.05)
        ring_devices = set(builder.SHARED_DEVICE_POOL)
        used = {agents_for_device for agents_for_device in builder.last_graph._agent_devices.keys()}
        assert used & ring_devices


class TestAblationVariants:
    def test_raw_plus_dtl_excludes_graph_and_semantic(self):
        variants = {v["id"]: v for v in build_variants()}
        h_features = set(variants["H_raw_plus_dtl"]["features"])
        assert h_features.isdisjoint(FEATURE_GROUPS["graph"])
        assert h_features.isdisjoint(FEATURE_GROUPS["semantic"])
        assert h_features.isdisjoint(FEATURE_GROUPS["security"])
        assert set(FEATURE_GROUPS["raw_transaction"]) <= h_features
        for g in DTL_ONLY_GROUPS:
            assert set(FEATURE_GROUPS[g]) <= h_features

    def test_raw_plus_dtl_plus_graph_adds_exactly_the_graph_group(self):
        variants = {v["id"]: v for v in build_variants()}
        h_features = set(variants["H_raw_plus_dtl"]["features"])
        i_features = set(variants["I_raw_plus_dtl_plus_graph"]["features"])
        assert i_features - h_features == set(FEATURE_GROUPS["graph"])

    def test_all_variant_ids_are_unique(self):
        ids = [v["id"] for v in build_variants()]
        assert len(ids) == len(set(ids))
