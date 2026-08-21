"""
Agent Intent Firewall.

Sits conceptually between the delegated authority and the agent's executed
actions: for every transaction, reshapes the SemanticDriftProof objects the
DTL invariant engine already computed into a multi-dimensional drift vector
and an ALLOW / PARTIAL_DRIFT / HARD_DRIFT verdict.

This module does not re-implement drift detection - `dtl/invariant_engine.py`
is the single source of truth for what counts as a violation on each
dimension. The firewall's job is purely the reshaping + verdict layer on top,
which is what a judge or an operator actually reasons about in the moment: not
"which invariant code fired" but "how far did this action drift, on every axis
at once, and what does that mean for what happens next".
"""

from .drift_engine import DRIFT_KEYS, compute_drift_vector
from .firewall_decision import evaluate

__all__ = ["compute_drift_vector", "evaluate", "DRIFT_KEYS"]
