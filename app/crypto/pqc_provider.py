"""
NIST FIPS 204 ML-DSA-44 post-quantum signature provider.

HONESTY CONTRACT
----------------
This module performs REAL lattice-based signing/verification. It contains NO
hash-based impostor construction. If no genuine FIPS 204 implementation can be
imported, the provider reports AVAILABLE=False and every call raises
`PQCUnavailableError`. Callers must surface "PQC MODULE UNAVAILABLE" in that
case and must never display "VERIFIED".

Provider resolution order:
  1. liboqs-python (`oqs`)  -- C reference implementation, preferred if the
     liboqs shared library is present on the host.
  2. dilithium-py (`dilithium_py.ml_dsa.ML_DSA_44`) -- pure-Python FIPS 204
     implementation. No native toolchain required.
"""

from __future__ import annotations

import hashlib
import os
from typing import Any, Dict, Optional, Tuple


class PQCUnavailableError(RuntimeError):
    """Raised when a genuine ML-DSA-44 implementation is not installed."""


# FIPS 204 ML-DSA-44 parameter set (Security Category 2).
_PK_BYTES = 1312
_SK_BYTES = 2560
_SIG_BYTES = 2420


def _resolve_backend() -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Returns (backend_id, backend_version, unavailable_reason)."""
    if os.environ.get("ENABLE_PQC", "1").lower() in ("0", "false", "no"):
        return None, None, "Disabled via ENABLE_PQC=0"

    # liboqs-python is probed only on explicit opt-in: importing it will try to
    # git-clone and cmake-build liboqs on hosts without the shared library,
    # which is slow and fails silently-ish. dilithium-py below is an equally
    # genuine FIPS 204 implementation and needs no native toolchain.
    if os.environ.get("FORSETI_TRY_LIBOQS", "0").lower() in ("1", "true", "yes"):
        try:
            import oqs  # type: ignore

            if "ML-DSA-44" in oqs.get_enabled_sig_mechanisms():
                return "liboqs", str(oqs.oqs_version()), None
        except Exception:
            pass

    try:
        from dilithium_py.ml_dsa import ML_DSA_44  # noqa: F401

        try:
            from importlib.metadata import version

            ver = version("dilithium-py")
        except Exception:
            ver = "unknown"
        return "dilithium-py", ver, None
    except Exception as exc:
        return None, None, f"No FIPS 204 ML-DSA implementation importable: {exc}"


_BACKEND, _BACKEND_VERSION, _UNAVAILABLE_REASON = _resolve_backend()


class MLDSA44Provider:
    """Genuine NIST FIPS 204 ML-DSA-44 signature operations."""

    ALGORITHM_NAME = "NIST FIPS 204 ML-DSA-44"
    SECURITY_CATEGORY = 2
    PUBLIC_KEY_SIZE = _PK_BYTES
    PRIVATE_KEY_SIZE = _SK_BYTES
    SIGNATURE_SIZE = _SIG_BYTES

    AVAILABLE: bool = _BACKEND is not None
    BACKEND: Optional[str] = _BACKEND
    BACKEND_VERSION: Optional[str] = _BACKEND_VERSION
    UNAVAILABLE_REASON: Optional[str] = _UNAVAILABLE_REASON

    @classmethod
    def provider_info(cls) -> Dict[str, Any]:
        return {
            "algorithm": cls.ALGORITHM_NAME,
            "standard": "NIST FIPS 204 (ML-DSA is the standardized successor to CRYSTALS-Dilithium)",
            "security_category": cls.SECURITY_CATEGORY,
            "available": cls.AVAILABLE,
            "backend": cls.BACKEND,
            "backend_version": cls.BACKEND_VERSION,
            "unavailable_reason": cls.UNAVAILABLE_REASON,
            "public_key_bytes": cls.PUBLIC_KEY_SIZE,
            "private_key_bytes": cls.PRIVATE_KEY_SIZE,
            "signature_bytes": cls.SIGNATURE_SIZE,
        }

    @classmethod
    def _require(cls) -> None:
        if not cls.AVAILABLE:
            raise PQCUnavailableError(cls.UNAVAILABLE_REASON or "ML-DSA-44 unavailable")

    @classmethod
    def generate_keypair(cls, seed: Optional[bytes] = None) -> Tuple[bytes, bytes]:
        """
        Generates an ML-DSA-44 (public_key, private_key) pair.

        `seed`, when supplied, is hashed to the 32-byte FIPS 204 key-generation
        seed xi so development keys are reproducible. Production keys must use
        the library CSPRNG (seed=None).
        """
        cls._require()

        if cls.BACKEND == "dilithium-py":
            from dilithium_py.ml_dsa import ML_DSA_44

            if seed is None:
                return ML_DSA_44.keygen()
            xi = hashlib.shake_256(b"FORSETI-ML-DSA-44-devseed" + seed).digest(32)
            return ML_DSA_44.key_derive(xi)

        import oqs  # type: ignore

        signer = oqs.Signature("ML-DSA-44")
        public_key = signer.generate_keypair()
        return public_key, signer.export_secret_key()

    @classmethod
    def sign(cls, message: bytes, private_key: bytes) -> bytes:
        """Produces a genuine ML-DSA-44 signature over `message`."""
        cls._require()

        if cls.BACKEND == "dilithium-py":
            from dilithium_py.ml_dsa import ML_DSA_44

            return ML_DSA_44.sign(private_key, message)

        import oqs  # type: ignore

        with oqs.Signature("ML-DSA-44", private_key) as signer:
            return signer.sign(message)

    @classmethod
    def verify(cls, message: bytes, signature: bytes, public_key: bytes) -> bool:
        """
        Verifies an ML-DSA-44 signature. Returns True only when the lattice
        verification equation holds for this exact (message, signature, key).
        """
        cls._require()

        if len(signature) != cls.SIGNATURE_SIZE or len(public_key) != cls.PUBLIC_KEY_SIZE:
            return False

        try:
            if cls.BACKEND == "dilithium-py":
                from dilithium_py.ml_dsa import ML_DSA_44

                return bool(ML_DSA_44.verify(public_key, message, signature))

            import oqs  # type: ignore

            with oqs.Signature("ML-DSA-44") as verifier:
                return bool(verifier.verify(message, signature, public_key))
        except Exception:
            return False
