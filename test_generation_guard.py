"""
test_generation_guard.py

This is the repeatable acceptance-test artifact for Sahaay's
interruption and recovery logic.

Run with:

    python -m pytest test_generation_guard.py -v
"""

import asyncio
import pytest

from generation_guard import GenerationGuard


@pytest.mark.asyncio
async def test_stale_tool_result_is_fenced_not_delivered():
    """
    A slow tool call from turn 1 is interrupted by turn 2.

    The old result must never reach on_result.
    The new result must be accepted.
    """

    guard = GenerationGuard()
    delivered = []

    async def slow_lookup(delay: float, value: str):
        await asyncio.sleep(delay)
        return value

    # Turn 1
    gen1 = guard.new_turn()

    task1 = guard.run(
        gen1,
        slow_lookup(0.3, "fever_protocol_result"),
        label="symptom_lookup_fever",
        on_result=delivered.append,
    )

    # User interrupts after 100 ms.
    await asyncio.sleep(0.1)

    # Turn 2
    gen2 = guard.new_turn()

    task2 = guard.run(
        gen2,
        slow_lookup(0.05, "cough_protocol_result"),
        label="symptom_lookup_cough",
        on_result=delivered.append,
    )

    await asyncio.gather(
        task1,
        task2,
        return_exceptions=True,
    )

    # The stale fever result must never be delivered.
    assert "fever_protocol_result" not in delivered

    # The corrected cough result must be delivered.
    assert "cough_protocol_result" in delivered

    # Audit trail must show cancellation and acceptance.
    outcomes = [event.outcome for event in guard.audit_log]

    assert "cancelled" in outcomes
    assert "accepted" in outcomes


@pytest.mark.asyncio
async def test_result_arriving_after_interruption_is_fenced_even_if_not_cancelled():
    """
    Belt-and-suspenders case.

    Simulates a backend request that cannot actually be aborted.
    Even if the old operation finishes after interruption, its result
    must not be delivered.
    """

    guard = GenerationGuard()
    delivered = []

    async def uncancellable_lookup():
        try:
            await asyncio.sleep(0.2)

        except asyncio.CancelledError:
            # Simulate a request that cannot actually be stopped.
            await asyncio.sleep(0.2)
            raise

        return "late_stale_value"

    gen1 = guard.new_turn()

    guard.run(
        gen1,
        uncancellable_lookup(),
        label="uncancellable",
        on_result=delivered.append,
    )

    # User interrupts.
    await asyncio.sleep(0.05)

    guard.new_turn()

    # Give the old task time to finish its simulated work.
    await asyncio.sleep(0.3)

    # Old result must never reach the user.
    assert "late_stale_value" not in delivered

    # Either it was fenced or explicitly cancelled.
    fenced = [
        event
        for event in guard.audit_log
        if event.outcome == "fenced_stale"
    ]

    assert len(fenced) >= 1 or any(
        event.outcome == "cancelled"
        for event in guard.audit_log
    )


@pytest.mark.asyncio
async def test_multiple_in_flight_tools_are_all_cancelled():
    """
    Multiple old-generation tool calls are running simultaneously.

    When the user interrupts, every old-generation task must be
    cancelled and none of their results may be delivered.
    """

    guard = GenerationGuard()
    delivered = []

    async def slow_lookup(value: str):
        await asyncio.sleep(0.3)
        return value

    # Generation 1.
    gen1 = guard.new_turn()

    task1 = guard.run(
        gen1,
        slow_lookup("old_fever_result"),
        label="fever_lookup",
        on_result=delivered.append,
    )

    task2 = guard.run(
        gen1,
        slow_lookup("old_cough_result"),
        label="cough_lookup",
        on_result=delivered.append,
    )

    # User interrupts while BOTH old requests are still running.
    await asyncio.sleep(0.05)

    # Generation 2.
    gen2 = guard.new_turn()

    # New generation gets a valid result.
    task3 = guard.run(
        gen2,
        slow_lookup("new_headache_result"),
        label="headache_lookup",
        on_result=delivered.append,
    )

    await asyncio.gather(
        task1,
        task2,
        task3,
        return_exceptions=True,
    )

    # Neither old result may reach the user.
    assert "old_fever_result" not in delivered
    assert "old_cough_result" not in delivered

    # New generation's result must be accepted.
    assert "new_headache_result" in delivered

    outcomes = [
        event.outcome
        for event in guard.audit_log
    ]

    # Both old tasks must be cancelled.
    assert outcomes.count("cancelled") >= 2

    # New task must be accepted.
    assert "accepted" in outcomes
