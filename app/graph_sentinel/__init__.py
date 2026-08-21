"""
Payment Graph Sentinel.

Every other feature group in feature_schema.py (delegation, cross-rail,
semantic, security) is computed from ONE authority's own state and history -
by construction, none of them can see a pattern that only exists ACROSS
authorities: many different agents converging on the same merchant, several
agents sharing a device fingerprint, or one agent's centrality in the overall
transaction graph. This package builds that cross-authority graph and turns
it into the "graph" feature group.

`PaymentGraph` is built incrementally by `detector/dataset_builder.py` as it
generates the synthetic training trajectory, and snapshotted BEFORE each
transaction is added (see `snapshot_features`) so no transaction's features
ever include its own effect on the graph - the same non-leakage discipline
`DTLFeatureExtractor` already applies to DTL/history features.

Live arena rounds (single authority, no cross-authority graph) legitimately
have no such signal to offer; `feature_schema.py` documents that these
features default to 0.0 there rather than being fabricated.
"""

from .graph_builder import GRAPH_FEATURE_NAMES, PaymentGraph

__all__ = ["PaymentGraph", "GRAPH_FEATURE_NAMES"]
