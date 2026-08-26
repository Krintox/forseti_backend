"""
Attested biller directory - the counterpart to `sku_catalogue.py`, for WHO gets
paid rather than WHAT is bought.

Why this module exists
----------------------
Adversarial review's F-19 called BENEFICIARY_DRIFT "the most realistic adversary
model in the entire set" and then landed the correct criticism on it:

    "It has no mechanism - the 'spoofed lookup' is narrated in the docstring and
    implemented as a different string literal, so nothing about how the agent got
    the wrong VPA is modelled."

That was accurate. The vector hardcoded `vpa_regional-collections-utility@upi`
and told the reader to imagine a poisoned lookup. A judge asking "so how did the
agent end up with that VPA?" would have got a shrug.

Biller substitution is not a mystery in the real world - it is a directory
problem. The agent asks "what is the VPA for the State Electricity Board?" and
something answers. If the thing that answers can be influenced, the amount, the
rail and the MCC all stay exactly as authorised and the money still leaves.
FBI IC3 reporting on beneficiary redirection is about precisely this class of
failure, and it costs more than card fraud.

So this module IS the lookup. The attack works by putting a plausible record
into it, the same way it works in reality, and the vector then resolves the
beneficiary rather than asserting it.

The trust inversion
-------------------
The important design point is the same one `sku_catalogue.py` makes: the
authority to say "this VPA belongs to the State Electricity Board" must not rest
with whoever is asking to be paid. Records here carry an ATTESTOR and a digest.
A record without one resolves, because refusing to pay anyone unlisted would
break ordinary commerce - but it resolves as UNATTESTED, and that fact reaches
the proof object instead of being lost.

What this is NOT
----------------
Not a real biller registry, not NPCI's, not connected to anything. It is a small
in-memory model whose only job is to make the causal chain of a substitution
attack real enough to inspect, rather than narrated.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional


@dataclass(frozen=True)
class BillerRecord:
    """One directory entry: a name an agent might search for, and where it pays."""

    biller_id: str
    legal_name: str
    vpa: str
    category_mcc: str
    #: Who asserts this mapping. `None` means nobody did - the record got in
    #: some other way, which is exactly the case worth surfacing.
    attestor: Optional[str] = None
    registered_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc), compare=False
    )

    @property
    def attested(self) -> bool:
        return self.attestor is not None

    def digest(self) -> str:
        """Stable fingerprint of the mapping, for the proof object."""
        payload = f"{self.biller_id}|{self.legal_name}|{self.vpa}|{self.attestor or 'UNATTESTED'}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class Resolution:
    """What the lookup returned, and how much it can be trusted."""

    query: str
    vpa: Optional[str]
    record: Optional[BillerRecord]
    attested: bool
    #: Other records that also matched the query. More than zero is the signal:
    #: a directory with two plausible answers for one name is a directory that
    #: has been polluted, whether deliberately or by accident.
    competing_matches: List[BillerRecord]
    basis: str

    @property
    def ambiguous(self) -> bool:
        return len(self.competing_matches) > 0


# --------------------------------------------------------------------------
# The attested baseline. Everything here has a named attestor.
# --------------------------------------------------------------------------

_SEED: Dict[str, BillerRecord] = {
    "biller_electricity_board": BillerRecord(
        biller_id="biller_electricity_board",
        legal_name="State Electricity Board",
        vpa="vpa_electricity_board@upi",
        category_mcc="4900",
        attestor="utility-registry-attestor",
    ),
    "biller_city_water": BillerRecord(
        biller_id="biller_city_water",
        legal_name="City Water Supply Authority",
        vpa="vpa_city_water@upi",
        category_mcc="4900",
        attestor="utility-registry-attestor",
    ),
    "biller_gas_distribution": BillerRecord(
        biller_id="biller_gas_distribution",
        legal_name="Municipal Gas Distribution",
        vpa="vpa_municipal_gas@upi",
        category_mcc="4900",
        attestor="utility-registry-attestor",
    ),
}

_DIRECTORY: Dict[str, BillerRecord] = dict(_SEED)


def reset() -> None:
    """Restores the attested baseline, discarding anything injected."""
    _DIRECTORY.clear()
    _DIRECTORY.update(_SEED)


def all_records() -> List[BillerRecord]:
    return list(_DIRECTORY.values())


def register_attested(record: BillerRecord) -> BillerRecord:
    if not record.attested:
        raise ValueError("register_attested requires an attestor; use register_unverified")
    _DIRECTORY[record.biller_id] = record
    return record


def register_unverified(
    *, biller_id: str, legal_name: str, vpa: str, category_mcc: str = "4900"
) -> BillerRecord:
    """
    Adds a record nobody attested.

    THIS IS THE ATTACK MECHANISM. A biller-substitution attack does not need to
    break cryptography or forge a mandate; it needs the agent's directory to
    contain a plausible second answer for a name the human already trusts. The
    vector calls this, and then simply asks the directory a normal question.
    """
    record = BillerRecord(
        biller_id=biller_id, legal_name=legal_name, vpa=vpa,
        category_mcc=category_mcc, attestor=None,
    )
    _DIRECTORY[biller_id] = record
    return record


def _tokens(text: str) -> List[str]:
    keep = "".join(ch.lower() if ch.isalnum() else " " for ch in text)
    return [t for t in keep.split() if t]


def resolve(query: str) -> Resolution:
    """
    Answers "what VPA should I pay for <name>?" the way an agent tool would.

    Matching is deliberately a naive token-overlap score, because that is what
    makes the attack work and pretending otherwise would be dishonest: a name
    like "State Electricity Board (Regional Collections)" shares every
    meaningful token with the attested "State Electricity Board", so it scores
    at least as well. Ranking is stable and prefers ATTESTED records on a tie,
    so the substitution only wins when the lookalike is a *closer* textual match
    - which is exactly the property that makes these names effective.
    """
    q = set(_tokens(query))
    if not q:
        return Resolution(query, None, None, False, [], "empty query")

    scored = []
    for record in _DIRECTORY.values():
        tokens = set(_tokens(record.legal_name))
        overlap = len(q & tokens)
        if not overlap:
            continue
        # Favour records whose own name is well covered by the query, so a
        # longer lookalike name that the query fully contains outranks the
        # shorter canonical one.
        coverage = overlap / len(tokens)
        scored.append((overlap, coverage, record.attested, record))

    if not scored:
        return Resolution(query, None, None, False, [], f"no directory entry matched {query!r}")

    scored.sort(key=lambda row: (row[0], row[1], row[2]), reverse=True)
    best = scored[0][3]
    competing = [row[3] for row in scored[1:]]

    if best.attested:
        basis = f"attested by {best.attestor} (digest {best.digest()})"
    else:
        basis = (
            f"UNATTESTED directory entry {best.biller_id} (digest {best.digest()}) - "
            f"no registry asserted this mapping"
        )
    if competing:
        basis += f"; {len(competing)} competing entry(ies) matched the same query"

    return Resolution(
        query=query, vpa=best.vpa, record=best,
        attested=best.attested, competing_matches=competing, basis=basis,
    )


def classify_beneficiary(vpa: str) -> Dict[str, object]:
    """
    Looks a VPA back up, so a proof can say what the payee actually is rather
    than only that it was not the authorised one.
    """
    for record in _DIRECTORY.values():
        if record.vpa == vpa:
            return {
                "known": True,
                "attested": record.attested,
                "legal_name": record.legal_name,
                "biller_id": record.biller_id,
                "basis": (
                    f"attested by {record.attestor} (digest {record.digest()})"
                    if record.attested
                    else f"present in the directory but UNATTESTED (digest {record.digest()})"
                ),
            }
    return {
        "known": False,
        "attested": False,
        "legal_name": None,
        "biller_id": None,
        "basis": f"{vpa} is not in the biller directory at all",
    }
