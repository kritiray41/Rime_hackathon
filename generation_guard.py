"""
generation_guard.py

The core of Sahaay's "interruption and recovery" claim.

Problem: in a naive voice agent, when a user interrupts and corrects
themselves, three things can go wrong even if you stop the audio:
  1. The in-flight LLM/tool call for the OLD request keeps running and its
     result gets appended to conversation history or spoken later.
  2. A slow tool call (e.g. a patient-record lookup) started for turn N
     resolves AFTER turn N+1 has already started, and its result silently
     leaks into the new turn.
  3. Conversation state (what the model thinks was said) drifts from what
     the user actually heard, so future turns reason over a lie.

GenerationGuard fixes this with one idea: every user turn gets a
monotonically increasing generation ID. Every tool call and every TTS
stream is stamped with the ID that spawned it. Before a result is allowed
to touch conversation state or be spoken, it must prove its stamp still
matches the CURRENT generation. If the user has already moved on, the
result is logged as fenced and dropped — never spoken, never merged into
history.

This module has no dependency on LiveKit or Rime on purpose: it's the
part of the claim that must be provable in isolation, with a fast
deterministic test, independent of live audio timing.
"""

from __future__ import annotations

import asyncio
import time
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger("sahaay.guard")


@dataclass
class FenceEvent:
    """One row of the audit log the RIME_EVIDENCE.md test reads back."""

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
    Tracks the "active" conversational turn and fences stale work.

    Usage pattern:
        guard = GenerationGuard()

        # on every new user utterance (including interruptions):
        gen = guard.new_turn()

        # when kicking off a tool call or TTS stream for that turn:
        task = guard.run(gen, my_slow_coroutine(), label="patient_lookup")

        # if the result arrives after the user moved on, it is fenced
        # and dropped instead of being delivered to on_result.
    """

    def __init__(self) -> None:
        self._current_gen = 0
        self._active_tasks: dict[int, list[asyncio.Task]] = {}
        self.audit_log: list[FenceEvent] = []

    @property
    def current_generation(self) -> int:
        return self._current_gen

    def new_turn(self) -> int:
        """
        Call this the instant a new user utterance is detected.

        Bumps the generation counter and cancels every task still running
        under the previous generation.
        """

        old_gen = self._current_gen
        self._current_gen += 1
        new_gen = self._current_gen

        for task in self._active_tasks.get(old_gen, []):
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

        self._active_tasks.pop(old_gen, None)

        return new_gen

    def run(
        self,
        gen: int,
        coro: Awaitable[Any],
        label: str,
        on_result: Optional[Callable[[Any], None]] = None,
    ) -> asyncio.Task:
        """
        Schedule coro under generation gen.

        If gen is still current when it completes, the result is accepted.

        If gen is no longer current, the result is fenced, logged, and
        dropped instead of being delivered to on_result.
        """

        async def _wrapped():
            try:
                result = await coro

            except asyncio.CancelledError:
                raise

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
                    "Fenced stale result from turn %s (now on %s): %s",
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

            if on_result:
                on_result(result)

            return result

        task = asyncio.ensure_future(_wrapped())

        task.sahaay_label = label  # type: ignore[attr-defined]

        self._active_tasks.setdefault(gen, []).append(task)

        return task

    def dump_audit_log(self, path: str) -> None:
        """Write the audit log as JSON Lines."""

        with open(path, "w") as f:
            for event in self.audit_log:
                f.write(event.to_json() + "\n")
