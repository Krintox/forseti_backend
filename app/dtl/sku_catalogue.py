"""
Attested SKU catalogue - an independent source of truth for what a line item
economically IS.

WHY THIS EXISTS. INV_02 ("does the basket match the authorised economic
purpose?") used to reason over two things the merchant controls: a free-text
category string, and an `is_stored_value` boolean. Both are supplied by the
same party the invariant is defending against. Measured evasion, no skill
required:

    "Amazon Gift Card" / GIFT_CARD / is_stored_value=True   -> caught
    "Prepaid Value Instrument" / MERCHANDISE / False        -> silent
    "Flexi Credit Top-Up" / GENERAL / False                 -> silent

A keyword list cannot defend a boundary where the adversary writes the words.
The defensible version - and the one the codebase already gestured at with a
`STRICT_CATALOG_ATTESTATION` policy that nothing read - is a catalogue whose
category assertions come from somewhere other than the merchant.

WHAT THIS IS NOT. The attestations here are synthetic: an in-repo dict with a
SHA-256 digest standing in for an issuer/scheme signature. There is no PKI, no
real attestor, and no revocation. It models the TRUST DIRECTION (category comes
from an independent party, not the counterparty) rather than a credential
format. Say that plainly rather than calling it signed catalogue attestation.

The real-world analogue is a scheme- or aggregator-maintained product taxonomy:
the reason `merchant_category_code` is assigned by the acquirer and not typed in
by the merchant is precisely this trust problem, one level up.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass(frozen=True)
class SkuAttestation:
    """An independent assertion about what a SKU economically is."""
    sku: str
    attested_category: str
    is_liquid_value: bool          # convertible back to money / near-cash
    attestor: str

    def digest(self) -> str:
        """
        Stands in for an attestor signature. A merchant cannot mint one of
        these for a SKU it invented, which is the whole point - though in this
        synthetic model "cannot" means "the catalogue is not writable by the
        transaction path", not "is cryptographically prevented".
        """
        payload = f"{self.sku}|{self.attested_category}|{self.is_liquid_value}|{self.attestor}"
        return hashlib.sha256(payload.encode()).hexdigest()[:16]


# Categories that convert delegated authority back into spendable money.
LIQUID_CATEGORIES = {"STORED_VALUE", "GIFT_CARD", "CRYPTO_TOKEN", "PREPAID_VOUCHER", "STORE_CREDIT"}

_ATTESTOR = "synthetic_scheme_catalogue_v1"


def _entry(sku: str, category: str, liquid: bool) -> SkuAttestation:
    return SkuAttestation(sku=sku, attested_category=category,
                          is_liquid_value=liquid, attestor=_ATTESTOR)


# The catalogue. Deliberately includes the liquid instruments under SEVERAL
# innocuous-sounding names, because that is exactly the evasion being closed:
# what matters is the SKU's attested nature, not what the merchant calls it.
_CATALOGUE: Dict[str, SkuAttestation] = {a.sku: a for a in [
    # --- genuine grocery / household ---
    _entry("SKU_GROC_01", "GROCERY", False),
    _entry("SKU_GROC_02", "GROCERY", False),
    _entry("SKU_GROC_03", "GROCERY", False),
    _entry("SKU_GROC_WEEKLY", "GROCERY", False),
    _entry("SKU_GROC_MONTHLY", "GROCERY", False),
    _entry("SKU_MILK_GEN", "GROCERY", False),
    _entry("SKU_ELEC_BILL", "UTILITIES", False),
    _entry("SKU_ELEC_BILL_2", "UTILITIES", False),
    _entry("SKU_SPLIT_01", "RETAIL", False),
    _entry("SKU_TECH_01", "ELECTRONICS", False),
    _entry("SKU_MICRO_PROBE", "MISC", False),
    _entry("SKU_REVOC_DIG", "DIGITAL", False),
    # --- liquid value, whatever the merchant chooses to call it ---
    _entry("SKU_GIFT_DIGITAL", "GIFT_CARD", True),
    _entry("SKU_GIFT_SMALL", "GIFT_CARD", True),
    _entry("SKU_VOUCHER_01", "PREPAID_VOUCHER", True),
    _entry("SKU_STORE_CREDIT", "STORE_CREDIT", True),
    _entry("SKU_CRYPTO_01", "CRYPTO_TOKEN", True),
    _entry("SKU_PREPAID_TOPUP", "STORED_VALUE", True),
]}


def lookup(sku: str) -> Optional[SkuAttestation]:
    return _CATALOGUE.get(sku)


def is_attested(sku: str) -> bool:
    return sku in _CATALOGUE


def register(attestation: SkuAttestation) -> None:
    """Test/demo hook for extending the catalogue. Not reachable from a transaction."""
    _CATALOGUE[attestation.sku] = attestation


def classify_item(sku: str, merchant_category: str, merchant_flag: bool) -> Dict[str, object]:
    """
    Decides what a line item actually is, and how much that decision can be
    trusted.

    Returns:
        attested        - was an independent assertion available at all
        is_liquid       - the trusted answer to "is this near-cash"
        category        - the trusted category
        misdeclared     - merchant's claim disagrees with the attestation
        basis           - which source decided, for the proof object
    """
    entry = lookup(sku)
    if entry is not None:
        merchant_claims_liquid = bool(merchant_flag) or (
            merchant_category or ""
        ).upper() in LIQUID_CATEGORIES
        return {
            "attested": True,
            "is_liquid": entry.is_liquid_value,
            "category": entry.attested_category,
            # A merchant calling a gift card "MERCHANDISE" is itself evidence,
            # and is now visible instead of simply being believed.
            "misdeclared": entry.is_liquid_value and not merchant_claims_liquid,
            "basis": f"attested by {entry.attestor} (digest {entry.digest()})",
        }

    # Unattested SKU. The merchant's own words are all there is - which is
    # precisely the situation that cannot be trusted, so it is reported as
    # unverified rather than silently believed.
    merchant_liquid = bool(merchant_flag) or (merchant_category or "").upper() in LIQUID_CATEGORIES
    return {
        "attested": False,
        "is_liquid": merchant_liquid,
        "category": (merchant_category or "UNKNOWN").upper(),
        "misdeclared": False,
        "basis": "UNATTESTED - merchant-supplied category, independently unverified",
    }


def unattested_skus(skus: List[str]) -> List[str]:
    return [s for s in skus if not is_attested(s)]
