"""
`is_running` gates every control in the UI. It used to be cleared only on the
happy path at the end of a round, so any failure in between - most realistically
an SSE client closing its tab mid-run, which makes the event callback raise -
left the flag latched True and the entire arena disabled until the process was
restarted. `_preserve_policy` had the same shape of bug at campaign level.

These tests pin that both flags unwind on the failure path too.
"""

import asyncio

import pytest

from app.arena.orchestrator import ArenaBattleOrchestrator


class Hangup(RuntimeError):
    """Stands in for a client that closed its tab mid-round."""


def test_round_clears_is_running_when_the_client_disconnects():
    orch = ArenaBattleOrchestrator()
    assert orch.is_running is False

    async def hangs_up(_event):
        raise Hangup("client closed the tab")

    with pytest.raises(Hangup):
        asyncio.run(orch.run_round_stream(round_number=2, event_callback=hangs_up, speed=10.0))

    assert orch.is_running is False, (
        "is_running latched after a mid-round disconnect - the UI would stay "
        "disabled until the process restarts"
    )
    assert orch.get_state()["is_running"] is False


def test_campaign_clears_both_flags_when_the_client_disconnects():
    orch = ArenaBattleOrchestrator()

    async def hangs_up(_event):
        raise Hangup("client closed the tab")

    with pytest.raises(Hangup):
        asyncio.run(orch.run_campaign(round_numbers=[2, 3], event_callback=hangs_up, speed=10.0))

    assert orch.is_running is False
    assert orch._preserve_policy is False, (
        "_preserve_policy latched, so the next standalone round would silently "
        "inherit the campaign's escalated policy"
    )


def test_a_round_nested_in_a_campaign_does_not_report_the_campaign_finished():
    """The flag must survive each inner round, not flicker off between them."""
    orch = ArenaBattleOrchestrator()
    seen = []

    async def watch(_event):
        seen.append(orch.is_running)

    asyncio.run(orch.run_campaign(round_numbers=[2, 3], event_callback=watch, speed=10.0))

    assert seen, "no events were emitted"
    assert all(seen), "is_running dropped to False partway through the campaign"
    assert orch.is_running is False, "is_running should be clear once the campaign ends"


def test_a_clean_round_still_clears_the_flag():
    orch = ArenaBattleOrchestrator()
    asyncio.run(orch.run_round_stream(round_number=2, speed=10.0))
    assert orch.is_running is False
