"""
Attack taxonomy (the IDENTIFY layer).

The markdown matrix in docs/taxonomy.md is the single source of truth; this
module parses it into structured records and attaches implementation metadata.

Fifteen vectors are deeply implemented and executable. The remainder are
research / identify-layer entries. `implemented` distinguishes them
explicitly so nothing in the UI can imply we execute all of them.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional

from .paths import DOCS_DIR

TAXONOMY_MD = os.path.join(DOCS_DIR, "taxonomy.md")

CHANNEL_LABELS = {
    "C1": "Card present / card-not-present",
    "C2": "UPI & real-time rails",
    "C3": "Agentic AI / machine commerce",
    "C4": "Digital wallets / stored value",
    "C5": "Cross-border & settlement",
}

SURFACE_LABELS = {
    "S1": "Identity & onboarding",
    "S2": "Authentication & credentials",
    "S3": "Transaction authorization",
    "S4": "Merchant & catalog integrity",
    "S5": "Settlement & reconciliation",
    "S6": "Dispute & chargeback",
    "S7": "Agent-to-agent delegation",
    "S8": "Human-in-the-loop & social engineering",
}

# The vectors that actually execute, mapped to the module that runs them.
IMPLEMENTED: Dict[int, Dict[str, Any]] = {
    1: {
        "key": "CROSS_RAIL_SPLIT",
        "module": "app.redteam.vectors.cross_rail_split.CrossRailSplitVector",
        "defeated_by": "INV_01_GLOBAL_BUDGET_EXCEEDED",
        "severity": "CRITICAL",
        "agentic_relevance": "HIGH",
        "flagship": True,
    },
    2: {
        "key": "INTENT_LAUNDERING",
        "module": "app.redteam.vectors.intent_laundering.IntentLaunderingVector",
        "defeated_by": "INV_02_SEMANTIC_INTENT_DRIFT",
        "severity": "CRITICAL",
        "agentic_relevance": "HIGH",
        "flagship": False,
    },
    3: {
        "key": "BASELINE_POISONING",
        "module": "app.redteam.vectors.other_vectors.BaselinePoisoningVector",
        "defeated_by": "INV_02_SEMANTIC_INTENT_DRIFT",
        "severity": "HIGH",
        "agentic_relevance": "MEDIUM",
        "flagship": False,
    },
    4: {
        "key": "REVOCATION_FLOOD",
        "module": "app.redteam.vectors.other_vectors.RevocationFloodVector",
        "defeated_by": "INV_01_GLOBAL_BUDGET_EXCEEDED",
        "severity": "HIGH",
        "agentic_relevance": "HIGH",
        "flagship": False,
    },
    5: {
        "key": "VELOCITY_BURST",
        "module": "app.redteam.vectors.other_vectors.VelocitySpikeVector",
        "defeated_by": "INV_01_GLOBAL_BUDGET_EXCEEDED",
        "severity": "MEDIUM",
        "agentic_relevance": "HIGH",
        "flagship": False,
    },
    6: {
        "key": "SCOPE_CREEP",
        "module": "app.redteam.vectors.other_vectors.ScopeCreepVector",
        "defeated_by": "INV_03_UNAUTHORIZED_MCC",
        "severity": "HIGH",
        "agentic_relevance": "HIGH",
        "flagship": False,
    },
    # Attacks on the NON-monetary dimensions of the grant. These exist because
    # a delegation is not a number: an agent can stay inside the ceiling and
    # still act outside what the human authorised.
    53: {
        "key": "RAIL_SCOPE_VIOLATION",
        "module": "app.redteam.vectors.authority_scope.RailScopeViolationVector",
        "defeated_by": "INV_04_UNAUTHORIZED_RAIL",
        "severity": "HIGH",
        "agentic_relevance": "HIGH",
        "flagship": False,
    },
    54: {
        "key": "PER_TX_BREACH",
        "module": "app.redteam.vectors.authority_scope.PerTransactionBreachVector",
        "defeated_by": "INV_05_PER_TX_CAP_EXCEEDED",
        "severity": "MEDIUM",
        "agentic_relevance": "HIGH",
        "flagship": False,
    },
    55: {
        "key": "LAPSED_MANDATE",
        "module": "app.redteam.vectors.authority_scope.LapsedMandateVector",
        "defeated_by": "INV_06_AUTHORITY_EXPIRED",
        "severity": "HIGH",
        "agentic_relevance": "HIGH",
        "flagship": False,
    },
    56: {
        "key": "BENEFICIARY_DRIFT",
        "module": "app.redteam.vectors.beneficiary_drift.BeneficiaryDriftVector",
        "defeated_by": "INV_07_UNAUTHORIZED_BENEFICIARY",
        "severity": "HIGH",
        "agentic_relevance": "HIGH",
        "flagship": False,
    },
    # Deception Lab (Module 2): attacks on the AGENT'S OWN REASONING rather
    # than on delegated authority. "defeated_by" names the deception_lab
    # detector, not an INV_ code - these are a parallel concern to the DTL
    # invariants, not another row in the same table.
    57: {
        "key": "PROMPT_INJECTION",
        "module": "app.redteam.vectors.deception.PromptInjectionVector",
        "defeated_by": "DECEPTION_LAB:PROMPT_INJECTION",
        "severity": "HIGH",
        "agentic_relevance": "HIGH",
        "flagship": False,
    },
    58: {
        "key": "TOOL_OUTPUT_POISONING",
        "module": "app.redteam.vectors.deception.ToolOutputPoisoningVector",
        "defeated_by": "DECEPTION_LAB:TOOL_OUTPUT_POISONING",
        "severity": "HIGH",
        "agentic_relevance": "HIGH",
        "flagship": False,
    },
    59: {
        "key": "CONTEXT_MEMORY_POISONING",
        "module": "app.redteam.vectors.deception.ContextPoisoningVector",
        "defeated_by": "DECEPTION_LAB:CONTEXT_MEMORY_POISONING",
        "severity": "CRITICAL",
        "agentic_relevance": "HIGH",
        "flagship": False,
    },
    60: {
        "key": "AUTHORITY_IMPERSONATION",
        "module": "app.redteam.vectors.deception.AuthorityImpersonationVector",
        "defeated_by": "DECEPTION_LAB:AUTHORITY_IMPERSONATION",
        "severity": "CRITICAL",
        "agentic_relevance": "HIGH",
        "flagship": False,
    },
    61: {
        "key": "CONSTRAINT_EROSION",
        "module": "app.redteam.vectors.constraint_erosion.ConstraintErosionVector",
        "defeated_by": "INV_02_SEMANTIC_INTENT_DRIFT",
        "severity": "HIGH",
        "agentic_relevance": "HIGH",
        "flagship": False,
    },
}

_ROW = re.compile(r"^\|\s*\*{0,2}(\d+)\*{0,2}\s*\|(.+)$")


def _clean(cell: str) -> str:
    """Strips markdown emphasis and normalises whitespace."""
    text = cell.strip()
    text = re.sub(r"\*+", "", text)
    text = re.sub(r"_+", "", text)
    text = re.sub(r"`", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _codes(cell: str, valid: Dict[str, str]) -> List[str]:
    return [c for c in re.findall(r"[CS]\d", cell) if c in valid]


def parse_taxonomy(path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Parses the markdown matrix into structured vector records."""
    path = path or TAXONOMY_MD
    if not os.path.exists(path):
        return []

    vectors: List[Dict[str, Any]] = []
    seen: set[int] = set()

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            match = _ROW.match(line.strip())
            if not match:
                continue
            try:
                vector_id = int(match.group(1))
            except ValueError:
                continue
            cells = [c for c in match.group(2).split("|")]
            if len(cells) < 5 or vector_id in seen:
                continue
            seen.add(vector_id)

            name = _clean(cells[0])
            channel_cell, surface_cell = cells[1], cells[2]
            mechanism = _clean(cells[3])
            reference = _clean(cells[4]) if len(cells) > 4 else ""
            status_cell = _clean(cells[5]) if len(cells) > 5 else ""

            impl = IMPLEMENTED.get(vector_id)
            channels = _codes(channel_cell, CHANNEL_LABELS)
            surfaces = _codes(surface_cell, SURFACE_LABELS)

            vectors.append({
                "id": vector_id,
                "name": name,
                "channels": channels,
                "channel_labels": [CHANNEL_LABELS[c] for c in channels],
                "surfaces": surfaces,
                "surface_labels": [SURFACE_LABELS[s] for s in surfaces],
                "description": mechanism,
                "real_world_basis": reference,
                "implemented": impl is not None,
                "implementation_module": impl["module"] if impl else None,
                "strategy_key": impl["key"] if impl else None,
                "defeated_by_invariant": impl["defeated_by"] if impl else None,
                "severity": impl["severity"] if impl else _infer_severity(status_cell, mechanism),
                "agentic_relevance": impl["agentic_relevance"] if impl else _infer_agentic(channels, surfaces),
                "flagship": bool(impl and impl.get("flagship")),
                "simulation_status": "IMPLEMENTED / EXECUTABLE" if impl else "RESEARCH ONLY (identify layer, not executed)",
            })

    vectors.sort(key=lambda v: v["id"])
    return vectors


def _infer_severity(status_cell: str, mechanism: str) -> str:
    text = f"{status_cell} {mechanism}".lower()
    if any(w in text for w in ("deepfake", "quantum", "drain", "double-spend", "laundering")):
        return "HIGH"
    return "MEDIUM"


def _infer_agentic(channels: List[str], surfaces: List[str]) -> str:
    if "C3" in channels or "S7" in surfaces:
        return "HIGH"
    if "C4" in channels or "S3" in surfaces:
        return "MEDIUM"
    return "LOW"


TAXONOMY: List[Dict[str, Any]] = parse_taxonomy()


def taxonomy_summary() -> Dict[str, Any]:
    implemented = [v for v in TAXONOMY if v["implemented"]]
    by_channel: Dict[str, int] = {}
    by_surface: Dict[str, int] = {}
    for v in TAXONOMY:
        for c in v["channels"]:
            by_channel[c] = by_channel.get(c, 0) + 1
        for s in v["surfaces"]:
            by_surface[s] = by_surface.get(s, 0) + 1
    return {
        "total_vectors": len(TAXONOMY),
        "implemented_count": len(implemented),
        "research_only_count": len(TAXONOMY) - len(implemented),
        "implemented_ids": [v["id"] for v in implemented],
        "channels": CHANNEL_LABELS,
        "surfaces": SURFACE_LABELS,
        "count_by_channel": by_channel,
        "count_by_surface": by_surface,
        "honesty_note": ("Only the vectors marked IMPLEMENTED execute against the simulator. "
                         "The rest are researched identify-layer entries and are never presented "
                         "as running code."),
        "source": "docs/taxonomy.md",
    }
