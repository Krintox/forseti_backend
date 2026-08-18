"""
Development key store for the FORSETI PQC audit layer.

PROTOTYPE SCOPE: this is an in-memory / local-directory development key holder.
It is NOT an HSM and NOT a production key-management system. Private keys are
never committed (see .gitignore) and are regenerated on demand.
"""

from __future__ import annotations

import hashlib
import os
from typing import Optional, Tuple

from .pqc_provider import MLDSA44Provider, PQCUnavailableError

DEFAULT_KEY_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "private", "pqc_keys")
)


class DevKeyStore:
    """Loads or generates an ML-DSA-44 development keypair."""

    def __init__(self, key_dir: Optional[str] = None, seed: Optional[bytes] = None):
        self.key_dir = key_dir or os.environ.get("PQC_KEY_DIR", DEFAULT_KEY_DIR)
        self.seed = seed
        self._pk: Optional[bytes] = None
        self._sk: Optional[bytes] = None

    @property
    def available(self) -> bool:
        return MLDSA44Provider.AVAILABLE

    def load_or_create(self) -> Tuple[Optional[bytes], Optional[bytes]]:
        """
        Returns (public_key, private_key), or (None, None) when no genuine
        ML-DSA-44 backend is installed.
        """
        if self._pk is not None and self._sk is not None:
            return self._pk, self._sk
        if not MLDSA44Provider.AVAILABLE:
            return None, None

        pk_path = os.path.join(self.key_dir, "mldsa44_dev.pub")
        sk_path = os.path.join(self.key_dir, "mldsa44_dev.key")

        if os.path.exists(pk_path) and os.path.exists(sk_path):
            try:
                with open(pk_path, "rb") as f:
                    pk = f.read()
                with open(sk_path, "rb") as f:
                    sk = f.read()
                if len(pk) == MLDSA44Provider.PUBLIC_KEY_SIZE and len(sk) == MLDSA44Provider.PRIVATE_KEY_SIZE:
                    self._pk, self._sk = pk, sk
                    return pk, sk
            except Exception:
                pass

        try:
            pk, sk = MLDSA44Provider.generate_keypair(self.seed)
        except PQCUnavailableError:
            return None, None

        self._pk, self._sk = pk, sk
        return pk, sk

    def persist(self) -> Optional[str]:
        """Writes the dev keypair to the isolated local key directory."""
        pk, sk = self.load_or_create()
        if pk is None or sk is None:
            return None
        os.makedirs(self.key_dir, exist_ok=True)
        with open(os.path.join(self.key_dir, "mldsa44_dev.pub"), "wb") as f:
            f.write(pk)
        with open(os.path.join(self.key_dir, "mldsa44_dev.key"), "wb") as f:
            f.write(sk)
        return self.key_dir

    def fingerprint(self) -> Optional[str]:
        pk, _ = self.load_or_create()
        if pk is None:
            return None
        return hashlib.sha256(pk).hexdigest()[:16]
