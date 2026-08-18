"""
Tamper-evident post-quantum audit layer for the FORSETI Delegation-Trust Ledger.

Signs canonicalized DTL authority snapshots with genuine NIST FIPS 204
ML-DSA-44. When no real ML-DSA implementation is installed the module reports
"PQC MODULE UNAVAILABLE" and NEVER reports a verified status.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from .canonicalization import CanonicalSerializer
from .key_store import DevKeyStore
from .pqc_provider import MLDSA44Provider, PQCUnavailableError

UNAVAILABLE_STATUS = "PQC MODULE UNAVAILABLE"
VERIFIED_STATUS = "ML-DSA-44 VERIFIED"
FAILED_STATUS = "VERIFICATION FAILED"


class PQCDelegationAuditModule:
    """Post-quantum signing/verification of DTL snapshots."""

    def __init__(self, seed: bytes = b"FORSETI_GFF_2026_ML_DSA_DEV_KEY", key_dir: Optional[str] = None):
        self.key_store = DevKeyStore(key_dir=key_dir, seed=seed)
        self.pk, self.sk = self.key_store.load_or_create()
        self.available = self.pk is not None and self.sk is not None
        self.pk_fingerprint = self.key_store.fingerprint()

    def provider_status(self) -> Dict[str, Any]:
        info = MLDSA44Provider.provider_info()
        info["key_loaded"] = self.available
        info["public_key_fingerprint"] = f"0x{self.pk_fingerprint}" if self.pk_fingerprint else None
        info["status_label"] = "ML-DSA-44 ACTIVE" if self.available else UNAVAILABLE_STATUS
        return info

    def build_snapshot_payload(self, authority_state: Dict[str, Any], event_root: str = "0x0") -> Dict[str, Any]:
        """Deterministic canonical view of the DTL state that gets signed."""
        total_exp = authority_state.get("total_exposure_global")
        if total_exp is None:
            total_exp = authority_state.get("total_exposure", 0.0)
        total_exp = float(total_exp or 0.0)

        return {
            "authority_id": authority_state.get("authority_id", "auth_default"),
            "principal": authority_state.get("principal", "user@forseti.ai"),
            "global_budget_ceiling": float(authority_state.get("global_budget_ceiling", 10000.0)),
            "total_exposure": round(total_exp, 2),
            "active_policy": str(authority_state.get("active_policy", "STANDARD")),
            "event_root": event_root,
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
