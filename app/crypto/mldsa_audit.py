"""
Tamper-evident post-quantum audit layer for the FORSETI Delegation-Trust Ledger.

Signs canonicalized DTL authority snapshots with genuine NIST FIPS 204
ML-DSA-44. When no real ML-DSA implementation is installed the module reports
"PQC MODULE UNAVAILABLE" and NEVER reports a verified status.
"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from .canonicalization import CanonicalSerializer
from .key_store import DevKeyStore
from .pqc_provider import MLDSA44Provider, PQCUnavailableError

UNAVAILABLE_STATUS = "PQC MODULE UNAVAILABLE"
VERIFIED_STATUS = "ML-DSA-44 VERIFIED"
FAILED_STATUS = "VERIFICATION FAILED"


class PQCDelegationAuditModule:
    """
    Post-quantum signing/verification of DTL snapshots.

    KEY PROVENANCE - read this before making any claim about tamper-evidence.

    This module previously derived its keypair from a seed hardcoded in the
    source (`b"FORSETI_GFF_2026_ML_DSA_DEV_KEY"`). That is a real limitation
    worth naming precisely: anyone holding the repository held the private key
    and could forge any snapshot, so the audit trail was tamper-evident against
    ACCIDENTS and not against ADVERSARIES.

    The default is now a randomly generated per-process key, so a checked-out
    copy of this repo does NOT hold the signing key of any running instance.
    That is a genuine improvement and still not a production posture: the key
    lives in process memory and a gitignored local directory, not in an HSM,
    and anyone who can read the process can sign. The honest claim remains
    "prototype audit-signing layer with development keys", never "tamper-proof".

    `FORSETI_PQC_SEED` forces a deterministic key when reproducibility is what
    you actually want (tests, byte-identical demo artifacts). It is opt-in, so
    the insecure-but-reproducible path is a choice someone makes rather than
    the silent default it used to be.
    """

    def __init__(self, seed: Optional[bytes] = None, key_dir: Optional[str] = None):
        if seed is None:
            env_seed = os.environ.get("FORSETI_PQC_SEED")
            # Random per process. NOT reproducible across restarts by design -
            # a deterministic default is what made the private key public.
            seed = env_seed.encode() if env_seed else os.urandom(32)
            self.key_provenance = (
                "deterministic (FORSETI_PQC_SEED set - reproducible, NOT secret)"
                if env_seed else
                "randomly generated per process (not derived from repository contents)"
            )
        else:
            self.key_provenance = "explicit seed supplied by caller"
        self.key_store = DevKeyStore(key_dir=key_dir, seed=seed)
        self.pk, self.sk = self.key_store.load_or_create()
        self.available = self.pk is not None and self.sk is not None
        self.pk_fingerprint = self.key_store.fingerprint()

    def provider_status(self) -> Dict[str, Any]:
        info = MLDSA44Provider.provider_info()
        info["key_loaded"] = self.available
        info["public_key_fingerprint"] = f"0x{self.pk_fingerprint}" if self.pk_fingerprint else None
        info["status_label"] = "ML-DSA-44 ACTIVE" if self.available else UNAVAILABLE_STATUS
        # Surfaced so the UI can state the security posture rather than only
        # the algorithm name. "Genuine ML-DSA-44" and "tamper-proof" are
        # different claims, and the second one is not true here.
        info["key_provenance"] = self.key_provenance
        info["hsm_backed"] = False
        info["security_posture"] = (
            "Prototype audit-signing layer. The signing key lives in process memory and a "
            "gitignored local directory, not an HSM - tamper-EVIDENT against accidental or "
            "downstream modification, not against an adversary with host access."
        )
        return info

    def build_snapshot_payload(self, authority_state: Dict[str, Any], event_root: str = "0x0") -> Dict[str, Any]:
        """Deterministic canonical view of the DTL state that gets signed."""
        total_exp = authority_state.get("total_exposure_global")
        if total_exp is None:
            total_exp = authority_state.get("total_exposure", 0.0)
        total_exp = float(total_exp or 0.0)

        # .get("value", ...) unwraps a real Enum member; a plain string already
        # has no such attribute and passes through. `str()` alone previously
        # rendered a live DefensePolicy member as "DefensePolicy.STANDARD" -
        # the Python repr, not its value - which then got signed, hashed and
        # displayed verbatim in the audit UI.
        policy = authority_state.get("active_policy", "STANDARD")
        policy_name = str(getattr(policy, "value", policy))

        # WHAT THIS COMMITS TO, stated precisely because "we sign the DTL
        # snapshot" sounds broader than it is.
        #
        # The scalar fields below are the authority's economic state. They do
        # NOT include the invariant proofs, the transactions, or the
        # containment decisions - those are covered TRANSITIVELY through
        # `event_root`, which is the head of the SHA-256 hash chain over every
        # event the round emitted. Because each chain entry commits to its
        # predecessor, signing the head commits to the entire ordered history.
        #
        # That indirection is what makes the narrow payload sufficient, so it
        # is worth being explicit: without a genuine `event_root` this
        # signature would attest to five numbers and a timestamp.
        return {
            "authority_id": authority_state.get("authority_id", "auth_default"),
            "principal": authority_state.get("principal", "user@forseti.ai"),
            "global_budget_ceiling": float(authority_state.get("global_budget_ceiling", 10000.0)),
            "total_exposure": round(total_exp, 2),
            # The four buckets individually, not just their sum: a snapshot
            # that committed only to the total could not distinguish money held
            # from money settled, which is the distinction INV_01 turns on.
            "exposure_breakdown": {
                "settled": round(float(authority_state.get("cumulative_spent_settled", 0.0) or 0.0), 2),
                "authorized": round(float(authority_state.get("cumulative_spent_authorized", 0.0) or 0.0), 2),
                "pending": round(float(authority_state.get("pending_spend_global", 0.0) or 0.0), 2),
                "reserved": round(float(authority_state.get("reserved_spend_global", 0.0) or 0.0), 2),
            },
            "active_policy": policy_name,
            "event_root": event_root,
            "event_root_covers": (
                "SHA-256 hash chain over every event in this round - invariant proofs, "
                "transactions and containment decisions are committed to transitively"
            ),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def create_signed_snapshot(self, authority_state: Dict[str, Any], event_root: str = "0x0") -> Dict[str, Any]:
        """Canonicalizes and signs a DTL snapshot with ML-DSA-44."""
        snapshot_payload = self.build_snapshot_payload(authority_state, event_root)
        canonical_bytes = CanonicalSerializer.canonical_bytes(snapshot_payload)
        state_hash = hashlib.sha256(canonical_bytes).hexdigest()

        if not self.available:
            return {
                "snapshot_payload": snapshot_payload,
                "canonical_state_hash": state_hash,
                "signature_algorithm": MLDSA44Provider.ALGORITHM_NAME,
                "signature_hex": None,
                "public_key_fingerprint": None,
                "verification_status": UNAVAILABLE_STATUS,
                "is_cryptographically_valid": False,
                "pqc_available": False,
                "unavailable_reason": MLDSA44Provider.UNAVAILABLE_REASON,
            }

        try:
            signature_bytes = MLDSA44Provider.sign(canonical_bytes, self.sk)
            is_verified = MLDSA44Provider.verify(canonical_bytes, signature_bytes, self.pk)
        except PQCUnavailableError as exc:
            return {
                "snapshot_payload": snapshot_payload,
                "canonical_state_hash": state_hash,
                "signature_algorithm": MLDSA44Provider.ALGORITHM_NAME,
                "signature_hex": None,
                "public_key_fingerprint": None,
                "verification_status": UNAVAILABLE_STATUS,
                "is_cryptographically_valid": False,
                "pqc_available": False,
                "unavailable_reason": str(exc),
            }

        return {
            "snapshot_payload": snapshot_payload,
            "canonical_state_hash": state_hash,
            "signature_algorithm": MLDSA44Provider.ALGORITHM_NAME,
            "signature_backend": MLDSA44Provider.BACKEND,
            "signature_hex": signature_bytes.hex(),
            "signature_bytes_len": len(signature_bytes),
            "public_key_fingerprint": f"0x{self.pk_fingerprint}",
            "verification_status": VERIFIED_STATUS if is_verified else FAILED_STATUS,
            "is_cryptographically_valid": bool(is_verified),
            "pqc_available": True,
        }

    def verify_snapshot(self, snapshot_payload: Dict[str, Any], signature_hex: Optional[str]) -> bool:
        """Verifies an externally supplied snapshot + signature against our public key."""
        if not self.available or not signature_hex:
            return False
        canonical_bytes = CanonicalSerializer.canonical_bytes(snapshot_payload)
        try:
            return MLDSA44Provider.verify(canonical_bytes, bytes.fromhex(signature_hex), self.pk)
        except Exception:
            return False

    def run_tamper_test(self, authority_state: Dict[str, Any]) -> Dict[str, Any]:
        """
        Executes the four-case cryptographic integrity proof:
          1. untouched snapshot            -> must verify
          2. mutated exposure amount       -> must fail
          3. single flipped signature byte -> must fail
          4. verification under wrong key  -> must fail
        """
        if not self.available:
            return {
                "status": UNAVAILABLE_STATUS,
                "reason": MLDSA44Provider.UNAVAILABLE_REASON,
                "all_tamper_tests_passed": False,
                "tests_run": False,
            }

        signed = self.create_signed_snapshot(authority_state)
        canonical_bytes = CanonicalSerializer.canonical_bytes(signed["snapshot_payload"])
        sig_bytes = bytes.fromhex(signed["signature_hex"])

        # 1. Untampered snapshot must verify.
        valid_ok = MLDSA44Provider.verify(canonical_bytes, sig_bytes, self.pk)

        # 2. Mutated exposure amount must be rejected.
        tampered_payload = dict(signed["snapshot_payload"])
        tampered_payload["total_exposure"] = float(tampered_payload.get("total_exposure", 0.0) or 0.0) + 5000.0
        tampered_bytes = CanonicalSerializer.canonical_bytes(tampered_payload)
        payload_rejected = not MLDSA44Provider.verify(tampered_bytes, sig_bytes, self.pk)

        # 3. Flipped signature byte must be rejected.
        bad_sig = bytearray(sig_bytes)
        bad_sig[10] = (bad_sig[10] + 1) % 256
        sig_rejected = not MLDSA44Provider.verify(canonical_bytes, bytes(bad_sig), self.pk)

        # 4. A different public key must not verify our signature.
        other_pk, _ = MLDSA44Provider.generate_keypair(b"FORSETI_ADVERSARY_KEY")
        wrong_key_rejected = not MLDSA44Provider.verify(canonical_bytes, sig_bytes, other_pk)

        return {
            "tests_run": True,
            "backend": MLDSA44Provider.BACKEND,
            "valid_verification": bool(valid_ok),
            "tampered_payload_rejected": bool(payload_rejected),
            "tampered_signature_rejected": bool(sig_rejected),
            "wrong_public_key_rejected": bool(wrong_key_rejected),
            "all_tamper_tests_passed": bool(
                valid_ok and payload_rejected and sig_rejected and wrong_key_rejected
            ),
        }
