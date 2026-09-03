"""
generation_guard.py

The core of Sahaay's "interruption and recovery" claim.

Every user turn gets a monotonically increasing generation ID.
Tool calls and other asynchronous work are stamped with the generation
that created them. A result is accepted only if its generation is still
the current generation.

When a new turn begins:
    - the generation counter advances
    - running work from the previous generation is cancelled
    - stale work is never allowed to deliver its result

This module is intentionally independent of LiveKit and Rime so that
the interruption/recovery logic can be tested deterministically with
asyncio.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger("sahaay.guard")


@dataclass
class FenceEvent:
    """One audit-log entry describing what happened to asynchronous work."""

    generation_id: int
    current_generation_at_check: int
    outcome: str  # "accepted" | "fenced_stale" | "cancelled"
    label: str
    timestamp: float = field(default_factory=time.monotonic)

    def to_json(self) -> str:
        return json.dumps(
            {
                "generation_id": self.generation_id,
                "current_generation_at_check": self.current_generation_at_check,
                "outcome": self.outcome,
                "label": self.label,
                "timestamp": self.timestamp,
            }
        )


class GenerationGuard:
    """
    Tracks the active conversational generation and fences stale work.

    Usage:

        guard = GenerationGuard()

        # New user utterance / interruption:
        gen = guard.new_turn()

        # Start asynchronous work for that generation:
        task = guard.run(
            gen,
            my_coroutine(),
            label="patient_lookup",
            on_result=handle_result,
        )

    A result is delivered only when its generation is still current.
    """

    def __init__(self) -> None:
        self._current_gen = 0
        self._active_tasks: dict[int, list[asyncio.Task]] = {}
        self.audit_log: list[FenceEvent] = []

    @property
    def current_generation(self) -> int:
        """Return the generation currently considered active."""
        return self._current_gen

    def new_turn(self) -> int:
        """
        Start a new conversational generation.

        Any unfinished task belonging to the immediately previous
        generation is cancelled and recorded in the audit log.
        """

        old_gen = self._current_gen

        self._current_gen += 1
        new_gen = self._current_gen

        old_tasks = self._active_tasks.pop(old_gen, [])

        for task in old_tasks:
            if not task.done():
                task.cancel()

                self.audit_log.append(
                    FenceEvent(
                        generation_id=old_gen,
                        current_generation_at_check=new_gen,
                        outcome="cancelled",
                        label=getattr(task, "sahaay_label", "unknown"),
                    )
                )

        return new_gen

    def run(
        self,
        gen: int,
        coro: Awaitable[Any],
        label: str,
        on_result: Optional[Callable[[Any], None]] = None,
    ) -> asyncio.Task:
        """
        Run asynchronous work under generation ``gen``.

        If ``gen`` is already stale when this method is called, the work
        is rejected immediately and recorded as fenced.

        If ``gen`` becomes stale while the work is running, cancellation
        prevents delivery when possible, while the generation check
        provides a second safety barrier before ``on_result`` is called.
        """

        # Protect against accidentally starting work for an already
        # obsolete generation.
        if gen != self._current_gen:
            if hasattr(coro, "close"):
                coro.close()  # type: ignore[attr-defined]

            self.audit_log.append(
                FenceEvent(
                    generation_id=gen,
                    current_generation_at_check=self._current_gen,
                    outcome="fenced_stale",
                    label=label,
                )
            )

            logger.info(
                "Rejected stale work from generation %s "
                "(current generation is %s): %s",
                gen,
                self._current_gen,
                label,
            )

            async def _rejected() -> None:
                return None

            task = asyncio.ensure_future(_rejected())
            task.sahaay_label = label  # type: ignore[attr-defined]
            return task

        async def _wrapped() -> Any:
            try:
                result = await coro

            except asyncio.CancelledError:
                raise

            # Second safety barrier: even if the underlying operation
            # completed despite cancellation, never deliver a stale result.
            if gen != self._current_gen:
                self.audit_log.append(
                    FenceEvent(
                        generation_id=gen,
                        current_generation_at_check=self._current_gen,
                        outcome="fenced_stale",
                        label=label,
                    )
                )

                logger.info(
                    "Fenced stale result from generation %s "
                    "(current generation is %s): %s",
                    gen,
                    self._current_gen,
                    label,
                )

                return None

            self.audit_log.append(
                FenceEvent(
                    generation_id=gen,
                    current_generation_at_check=self._current_gen,
                    outcome="accepted",
                    label=label,
                )
            )

            if on_result is not None:
                on_result(result)

            return result

        task = asyncio.ensure_future(_wrapped())
        task.sahaay_label = label  # type: ignore[attr-defined]

        self._active_tasks.setdefault(gen, []).append(task)

        # Remove this task from the active-task registry once it finishes.
        def _cleanup(completed_task: asyncio.Task) -> None:
            tasks = self._active_tasks.get(gen)

            if tasks is None:
                return

            try:
                tasks.remove(completed_task)
            except ValueError:
                pass

            if not tasks:
                self._active_tasks.pop(gen, None)

        task.add_done_callback(_cleanup)

        return task

    def dump_audit_log(self, path: str) -> None:
        """Write the audit log as JSON Lines."""

        with open(path, "w") as f:
            for event in self.audit_log:
                f.write(event.to_json() + "\n")
