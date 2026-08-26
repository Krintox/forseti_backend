"""
The running entity graph and the feature snapshot computed from it.

Nodes are agents, merchants and (implicitly, via `_agent_devices`) devices.
Edges are agent-merchant pairs, weighted by how many transactions have run
between them so far. Global graph algorithms (PageRank, betweenness
centrality, Louvain community detection) are recomputed every
`REFRESH_EVERY` transactions rather than on every single one: on a graph
that ends a full training run with tens of thousands of edges, recomputing
per-row would dominate generation time for no measurable benefit, since none
of these metrics move meaningfully between two consecutive transactions on a
graph this size. That is a deliberate batching decision, not an accuracy
shortcut - it is documented here rather than left implicit.
"""

from __future__ import annotations

from typing import Dict, Optional, Set

import networkx as nx
from networkx.algorithms.community import louvain_communities

REFRESH_EVERY = 200

GRAPH_FEATURE_NAMES = [
    "graph_agent_out_degree",
    "graph_merchant_in_degree",
    "graph_agent_pagerank",
    "graph_merchant_pagerank",
    "graph_agent_betweenness",
    "graph_community_size_ratio",
    "graph_device_shared_count",
]


class PaymentGraph:
    """An incrementally-built agent<->merchant graph with periodic global metrics."""

    def __init__(self) -> None:
        self.graph = nx.Graph()
        self._agent_devices: Dict[str, Set[str]] = {}  # device_id -> agent_ids that used it
        self._tx_count = 0
        self._pagerank: Dict[str, float] = {}
        self._betweenness: Dict[str, float] = {}
        self._community_of: Dict[str, int] = {}
        self._community_sizes: Dict[int, int] = {}

    def node_count(self) -> int:
        return int(self.graph.number_of_nodes())

    def edge_count(self) -> int:
        return int(self.graph.number_of_edges())

    def transaction_count(self) -> int:
        return int(self._tx_count)

    @staticmethod
    def _agent_node(agent_id: str) -> str:
        return f"agent::{agent_id}"

    @staticmethod
    def _merchant_node(merchant_id: str) -> str:
        return f"merchant::{merchant_id}"

    def snapshot_features(
        self, agent_id: str, merchant_id: str, device_id: Optional[str] = None
    ) -> Dict[str, float]:
        """
        Features computed from the graph AS IT EXISTS before this transaction
        is added - call this before `add_transaction` for the same tx, or the
        transaction's own edge would leak into its own features.
        """
        a_node, m_node = self._agent_node(agent_id), self._merchant_node(merchant_id)
        total_nodes = max(1, self.graph.number_of_nodes())

        community = self._community_of.get(a_node)
        community_size_ratio = (
            float(self._community_sizes.get(community, 1)) / total_nodes
            if community is not None else 0.0
        )
        device_shared_count = (
            float(len(self._agent_devices.get(device_id, ()))) if device_id else 0.0
        )

        return {
            "graph_agent_out_degree": float(self.graph.degree(a_node)) if a_node in self.graph else 0.0,
            "graph_merchant_in_degree": float(self.graph.degree(m_node)) if m_node in self.graph else 0.0,
            "graph_agent_pagerank": float(self._pagerank.get(a_node, 0.0)),
            "graph_merchant_pagerank": float(self._pagerank.get(m_node, 0.0)),
            "graph_agent_betweenness": float(self._betweenness.get(a_node, 0.0)),
            "graph_community_size_ratio": community_size_ratio,
            "graph_device_shared_count": device_shared_count,
        }

    def add_transaction(self, agent_id: str, merchant_id: str, device_id: Optional[str] = None) -> None:
        a_node, m_node = self._agent_node(agent_id), self._merchant_node(merchant_id)
        if self.graph.has_edge(a_node, m_node):
            self.graph[a_node][m_node]["weight"] += 1
        else:
            self.graph.add_edge(a_node, m_node, weight=1)
        if device_id:
            self._agent_devices.setdefault(device_id, set()).add(agent_id)

        self._tx_count += 1
        if self._tx_count % REFRESH_EVERY == 0:
            self.refresh_global_metrics()

    def refresh_global_metrics(self) -> None:
        """Recomputes PageRank, betweenness and Louvain communities from scratch."""
        if self.graph.number_of_nodes() < 2:
            return
        self._pagerank = nx.pagerank(self.graph, weight="weight")
        # Betweenness is O(V*E) exactly; k-sampling keeps it tractable as the
        # graph grows across a full training run without changing its meaning
        # (it is the standard networkx approximation, seeded for reproducibility).
        k = min(200, self.graph.number_of_nodes())
        self._betweenness = nx.betweenness_centrality(self.graph, k=k, weight="weight", seed=42)

        communities = louvain_communities(self.graph, weight="weight", seed=42)
        self._community_of = {}
        self._community_sizes = {}
        for idx, members in enumerate(communities):
            self._community_sizes[idx] = len(members)
            for node in members:
                self._community_of[node] = idx

    def stats(self) -> Dict[str, int]:
        return {
            "nodes": self.graph.number_of_nodes(),
            "edges": self.graph.number_of_edges(),
            "transactions_ingested": self._tx_count,
            "communities": len(self._community_sizes),
            "distinct_devices": len(self._agent_devices),
        }
