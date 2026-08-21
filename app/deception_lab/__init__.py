"""
Agentic Payment Deception Lab.

Every other security layer in FORSETI (dtl/, intent_firewall/) asks whether an
ACTION is inside the delegated authority. This package asks a different
question: was the AGENT that decided to take the action fed a false premise -
a spoofed merchant instruction, a poisoned tool result, a fabricated memory of
prior approval, or a self-issued escalation?

The point being demonstrated is specifically that none of this matters to the
outcome: every detector here re-derives the truth from data no deception can
touch (the structured CartItem list, the live signed DTL grant, the
authorization chain) rather than trusting anything the agent was told. An LLM
agent COULD be fooled by the deceptive input; the deterministic system next to
it never consults that input for anything security-relevant in the first
place.
"""

from .detectors import evaluate_all

__all__ = ["evaluate_all"]
