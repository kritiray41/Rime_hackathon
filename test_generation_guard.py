"""
test_generation_guard.py

Repeatable acceptance and stress tests for Sahaay's
interruption and recovery logic.

Run with:

    python -m pytest test_generation_guard.py -v -s
"""

import asyncio
import time

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

    gen1 = guard.new_turn()

    task1 = guard.run(
        gen1,
        slow_lookup(0.3, "fever_protocol_result"),
        label="symptom_lookup_fever",
        on_result=delivered.append,
    )

    await asyncio.sleep(0.1)

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

    assert "fever_protocol_result" not in delivered
    assert "cough_protocol_result" in delivered

    outcomes = [event.outcome for event in guard.audit_log]

    assert "cancelled" in outcomes
    assert "accepted" in outcomes


@pytest.mark.asyncio
async def test_result_arriving_after_interruption_is_fenced_even_if_not_cancelled():
    """
    Simulates a backend operation that cannot actually be aborted.

    Even if the old operation finishes after interruption, its stale
    result must never be delivered.
    """

    guard = GenerationGuard()
    delivered = []

    async def uncancellable_lookup():
        try:
            await asyncio.sleep(0.2)

        except asyncio.CancelledError:
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

    await asyncio.sleep(0.05)

    guard.new_turn()

    await asyncio.sleep(0.3)

    assert "late_stale_value" not in delivered

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

    await asyncio.sleep(0.05)

    gen2 = guard.new_turn()

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

    assert "old_fever_result" not in delivered
    assert "old_cough_result" not in delivered
    assert "new_headache_result" in delivered

    outcomes = [
        event.outcome
        for event in guard.audit_log
    ]

    assert outcomes.count("cancelled") >= 2
    assert "accepted" in outcomes


@pytest.mark.asyncio
async def test_rapid_double_interrupt_only_latest_generation_survives():
    """
    Stress case: the user interrupts twice in rapid succession.

    Generation 1 starts work.
    Generation 2 starts work.
    Generation 3 becomes the latest generation.

    Only generation 3's result may be delivered.
    """

    guard = GenerationGuard()
    delivered = []

    async def slow_lookup(value: str):
        await asyncio.sleep(0.2)
        return value

    gen1 = guard.new_turn()

    task1 = guard.run(
        gen1,
        slow_lookup("generation_1_result"),
        label="generation_1_lookup",
        on_result=delivered.append,
    )

    await asyncio.sleep(0.03)

    gen2 = guard.new_turn()

    task2 = guard.run(
        gen2,
        slow_lookup("generation_2_result"),
        label="generation_2_lookup",
        on_result=delivered.append,
    )

    await asyncio.sleep(0.03)

    gen3 = guard.new_turn()

    task3 = guard.run(
        gen3,
        slow_lookup("generation_3_result"),
        label="generation_3_lookup",
        on_result=delivered.append,
    )

    await asyncio.gather(
        task1,
        task2,
        task3,
        return_exceptions=True,
    )

    assert "generation_1_result" not in delivered
    assert "generation_2_result" not in delivered
    assert "generation_3_result" in delivered

    outcomes = [
        event.outcome
        for event in guard.audit_log
    ]

    assert outcomes.count("cancelled") >= 2
    assert "accepted" in outcomes


@pytest.mark.asyncio
async def test_interruption_with_zero_tools_in_flight():
    """
    Stress case: the user interrupts when no tools are active.

    The generation counter must still advance safely.
    """

    guard = GenerationGuard()

    gen1 = guard.new_turn()
    gen2 = guard.new_turn()

    assert gen1 == 1
    assert gen2 == 2
    assert guard.current_generation == 2

    assert guard.audit_log == []


@pytest.mark.asyncio
async def test_three_rapid_generations_only_latest_result_is_delivered():
    """
    Three generations are created rapidly.

    Only the final generation's result may reach on_result.
    """

    guard = GenerationGuard()
    delivered = []

    async def lookup(delay: float, value: str):
        await asyncio.sleep(delay)
        return value

    gen1 = guard.new_turn()

    task1 = guard.run(
        gen1,
        lookup(0.15, "old_generation_1"),
        label="lookup_generation_1",
        on_result=delivered.append,
    )

    await asyncio.sleep(0.02)

    gen2 = guard.new_turn()

    task2 = guard.run(
        gen2,
        lookup(0.15, "old_generation_2"),
        label="lookup_generation_2",
        on_result=delivered.append,
    )

    await asyncio.sleep(0.02)

    gen3 = guard.new_turn()

    task3 = guard.run(
        gen3,
        lookup(0.05, "current_generation_3"),
        label="lookup_generation_3",
        on_result=delivered.append,
    )

    await asyncio.gather(
        task1,
        task2,
        task3,
        return_exceptions=True,
    )

    assert "old_generation_1" not in delivered
    assert "old_generation_2" not in delivered
    assert "current_generation_3" in delivered

    assert guard.current_generation == 3


@pytest.mark.asyncio
async def test_latest_generation_result_is_accepted_after_interruption():
    """
    Measures local GenerationGuard acceptance latency.

    This is NOT a Rime audio latency measurement.
    """

    guard = GenerationGuard()
    delivered = []

    async def fast_lookup():
        await asyncio.sleep(0.01)
        return "latest_result"

    old_gen = guard.new_turn()

    old_task = guard.run(
        old_gen,
        asyncio.sleep(0.2),
        label="old_lookup",
        on_result=delivered.append,
    )

    await asyncio.sleep(0.02)

    interruption_time = time.monotonic()

    new_gen = guard.new_turn()

    new_task = guard.run(
        new_gen,
        fast_lookup(),
        label="latest_lookup",
        on_result=delivered.append,
    )

    await asyncio.gather(
        old_task,
        new_task,
        return_exceptions=True,
    )

    latency_ms = (time.monotonic() - interruption_time) * 1000

    assert "latest_result" in delivered

    assert "old_lookup" in [
        event.label
        for event in guard.audit_log
        if event.outcome == "cancelled"
    ]

    print(
        f"\nLatest-generation acceptance latency: "
        f"{latency_ms:.2f} ms"
    )


@pytest.mark.asyncio
async def test_old_generation_started_after_interruption_is_fenced():
    """
    Edge case: code accidentally tries to start work using an old
    generation after a newer generation has already started.

    The stale result must not reach on_result.
    """

    guard = GenerationGuard()
    delivered = []

    async def lookup():
        await asyncio.sleep(0.02)
        return "stale_generation_result"

    old_gen = guard.new_turn()

    # Move to a new generation before starting the old-generation work.
    new_gen = guard.new_turn()

    assert new_gen == old_gen + 1

    task = guard.run(
        old_gen,
        lookup(),
        label="late_old_generation",
        on_result=delivered.append,
    )

    await asyncio.gather(
        task,
        return_exceptions=True,
    )

    assert "stale_generation_result" not in delivered

    # The guard must record that the old generation was not accepted.
    outcomes = [
        event.outcome
        for event in guard.audit_log
    ]

    assert "accepted" not in outcomes

    assert any(
        event.outcome == "fenced_stale"
        for event in guard.audit_log
    ) or any(
        event.outcome == "cancelled"
        for event in guard.audit_log
    )
