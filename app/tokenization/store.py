"""
In-memory token store. Same persistence posture as dtl/ledger.py and the rest
of the demo runtime: process-lifetime state, not a database - see
docs/LEARN_15_KNOWN_GAPS_AND_DISCREPANCIES.md for the project-wide note on
why that is an accepted limitation for a research prototype.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from .models import TokenizedPaymentCredential


class TokenStore:
    def __init__(self) -> None:
        self._tokens: Dict[str, TokenizedPaymentCredential] = {}

    def add(self, token: TokenizedPaymentCredential) -> TokenizedPaymentCredential:
        self._tokens[token.token_id] = token
        return token

    def get(self, token_id: str) -> Optional[TokenizedPaymentCredential]:
        return self._tokens.get(token_id)

    def list(self) -> List[TokenizedPaymentCredential]:
        return sorted(self._tokens.values(), key=lambda t: t.issued_at, reverse=True)

    def reset(self) -> None:
        self._tokens.clear()
