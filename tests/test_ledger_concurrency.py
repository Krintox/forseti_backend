"""
Concurrency and exposure-lifecycle tests for the Delegation-Trust Ledger.

WHY THIS FILE EXISTS. The DTL's headline property is that it prevents three
rails from each approving against the same unspent headroom. An adversarial
review pointed out that this was asserted rather than demonstrated: the arena
executes rail legs in a sequential `for` loop, so the race the ledger claims to
prevent could never occur in the first place, and three of the four exposure
buckets were permanently zero because nothing ever wrote to them.

"The hard problem was not solved; it was defined out of existence and then
claimed." That criticism was correct.

These tests do the opposite: they create genuine concurrency with real threads
hammering one authority, and assert the ceiling holds. A test that cannot fail
if the lock is removed is not evidence, so `test_check_then_act_would_overspend`
demonstrates the unsafe pattern overspending on the SAME ledger - proving the
protection is doing work rather than the scenario being harmless.
"""

import threading
import time

import pytest

from app.dtl.ledger import DTLLedger

AUTH = "auth_household_grocery_2026"


class TestExposureLifecycle:
    def test_all_four_buckets_are_reachable(self):
        led = DTLLedger()
        led.reset_authority(AUTH, budget=10000.0)

        led.register_pending_spend(AUTH, 3000.0)
        assert led.exposure_breakdown(AUTH)["pending"] == 3000.0

        led.finalize_authorized_spend(AUTH, 3000.0)
        b = led.exposure_breakdown(AUTH)
        assert b["pending"] == 0.0 and b["authorized"] == 3000.0

        led.finalize_settled_spend(AUTH, 1200.0)
        b = led.exposure_breakdown(AUTH)
        assert b["authorized"] == 1800.0 and b["settled"] == 1200.0
        # Total is conserved through every transition.
        assert b["total"] == 3000.0

    def test_a_hold_consumes_headroom_before_it_is_captured(self):
        """
        The property that closes the cross-rail window: money that has not
        moved yet is still committed authority. If holds did not count, three
        rails could each approve against the same headroom.
        """
        led = DTLLedger()
        led.reset_authority(AUTH, budget=10000.0)
        led.register_pending_spend(AUTH, 8000.0)
        assert led.exposure_breakdown(AUTH)["headroom"] == 2000.0
        granted, _ = led.try_reserve(AUTH, 5000.0)
        assert granted is False, "a hold failed to consume headroom"

    def test_released_hold_returns_headroom(self):
        """A contained transaction must not starve the agent of its grant."""
        led = DTLLedger()
        led.reset_authority(AUTH, budget=10000.0)
        led.register_pending_spend(AUTH, 4000.0)
        led.release_hold(AUTH, 4000.0)
        b = led.exposure_breakdown(AUTH)
        assert b["pending"] == 0.0 and b["headroom"] == 10000.0


class TestConcurrentAuthorization:
    def test_parallel_reservations_never_exceed_the_ceiling(self):
        """
        The actual claim, under actual concurrency: 60 threads race to reserve
        against a ceiling that only fits 10 of them. Exactly 10 may win.
        """
        led = DTLLedger()
        led.reset_authority(AUTH, budget=10000.0)

        granted_flags = []
        barrier = threading.Barrier(60)

        def worker():
            barrier.wait()          # maximise real contention
            ok, _ = led.try_reserve(AUTH, 1000.0)
            granted_flags.append(ok)

        threads = [threading.Thread(target=worker) for _ in range(60)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        b = led.exposure_breakdown(AUTH)
        assert sum(granted_flags) == 10, f"expected exactly 10 grants, got {sum(granted_flags)}"
        assert b["total"] == 10000.0
        assert b["total"] <= b["ceiling"], "aggregate exposure exceeded the delegated ceiling"

    def test_check_then_act_would_overspend(self):
        """
        Guards the guard. Reproduces the UNSAFE pattern - read headroom, then
        write - against the same ledger, and shows it overspending. Without
        this, `test_parallel_reservations_never_exceed_the_ceiling` might be
        passing because the scenario is benign rather than because the lock
        works.
        """
        led = DTLLedger()
        led.reset_authority(AUTH, budget=10000.0)
        barrier = threading.Barrier(60)

        def unsafe_worker():
            auth = led.get_authority(AUTH)
            barrier.wait()
            # Deliberately NOT atomic: the classic check-then-act bug.
            if auth.total_exposure_global + 1000.0 <= auth.global_budget_ceiling:
                # An explicit yield between the check and the write. A busy
                # loop is not enough here: CPython only switches threads every
                # `sys.getswitchinterval()` seconds (5 ms by default), so a few
                # hundred no-op iterations complete inside one slice and the
                # race never materialises. Sleeping forces the interleaving
                # that a real I/O-bound authorizer would hit naturally.
                time.sleep(0.002)
                auth.pending_spend_global += 1000.0

        threads = [threading.Thread(target=unsafe_worker) for _ in range(60)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        overspend = led.exposure_breakdown(AUTH)["total"]
        assert overspend > 10000.0, (
            f"check-then-act did not overspend (total={overspend}); the concurrency "
            f"test above may not be proving anything"
        )

    def test_mixed_lifecycle_operations_are_thread_safe(self):
        """Interleaved holds, captures and settlements must conserve the total."""
        led = DTLLedger()
        led.reset_authority(AUTH, budget=1_000_000.0)
        errors = []

        def worker(n: int):
            try:
                for _ in range(40):
                    ok, _ = led.try_reserve(AUTH, 100.0)
                    if ok:
                        led.finalize_authorized_spend(AUTH, 100.0)
                        led.finalize_settled_spend(AUTH, 40.0)
            except Exception as exc:      # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"lifecycle raised under concurrency: {errors}"
        b = led.exposure_breakdown(AUTH)
        # Every reserved 100 ends up split 40 settled / 60 authorized.
        assert b["pending"] == pytest.approx(0.0, abs=1e-6)
        assert b["total"] == pytest.approx(b["settled"] + b["authorized"], abs=1e-6)
        assert b["total"] <= b["ceiling"]
