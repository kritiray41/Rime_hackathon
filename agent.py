"""
agent.py — Sahaay: a voice-native assistant for rural health workers.

Lets a health worker record and retrieve patient information completely
hands-free, and — the hard voice problem this submission proves — when
she interrupts or corrects herself mid-turn, Rime's audio stops
immediately AND any in-flight tool result tied to the old request is
fenced so it can never be spoken or merged into conversation state.

Run:
    python agent.py dev

Requires (see .env.example):
    LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET
    RIME_API_KEY
    OPENAI_API_KEY   (STT + LLM — swap for your provider of choice)
"""

from __future__ import annotations

import asyncio
import logging
import time

from dotenv import load_dotenv
from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    RunContext,
    WorkerOptions,
    cli,
    function_tool,
)
from livekit.plugins import openai, rime, silero

from generation_guard import GenerationGuard

load_dotenv()
logger = logging.getLogger("sahaay.agent")

# One guard per session, created in entrypoint() and closed over by the tools.
_guard: GenerationGuard | None = None


def _fake_patient_lookup(query: str, delay_s: float) -> "asyncio.Future[str]":
    """
    Simulates a real backend call (patient record DB / symptom protocol
    lookup) with realistic latency, so the interruption stress case has
    something slow enough to actually race against.
    """
    async def _do_lookup():
        await asyncio.sleep(delay_s)
        return f"Protocol result for '{query}': standard first-aid steps apply."
    return asyncio.ensure_future(_do_lookup())


class SahaayAssistant(Agent):
    def __init__(self) -> None:
        super().__init__(
            instructions=(
                "You are Sahaay, a calm, concise voice assistant for a rural "
                "community health worker (ASHA) who is speaking to you "
                "hands-free while attending to a patient. Keep every spoken "
                "response to 1-2 short sentences. Never read out technical "
                "jargon. If the worker corrects or interrupts herself, treat "
                "her latest statement as the ground truth and do not refer "
                "back to whatever you were about to say."
            )
        )

    @function_tool()
    async def lookup_symptom_protocol(
        self, context: RunContext, symptom: str
    ) -> str:
        """
        Look up the first-aid protocol for a reported symptom. This call is
        deliberately slow (simulating a real lookup) and is stamped with the
        turn's generation ID so a stale result can never be spoken if the
        user has already moved on to a different symptom.
        """
        assert _guard is not None
        gen = _guard.current_generation
        t0 = time.monotonic()

        result_holder: dict[str, str] = {}

        def _capture(value: str) -> None:
            result_holder["value"] = value

        task = _guard.run(
            gen,
            _fake_patient_lookup(symptom, delay_s=2.0),
            label=f"symptom_lookup:{symptom}",
            on_result=_capture,
        )
        try:
            await task
        except asyncio.CancelledError:
            logger.info("lookup for %s cancelled (turn %s superseded)", symptom, gen)
            raise

        elapsed = time.monotonic() - t0
        if "value" not in result_holder:
            # Fenced — the guard decided this result is stale. Tell the LLM
            # explicitly rather than silently returning nothing, so it
            # doesn't hallucinate a value.
            logger.info("Result for '%s' fenced after %.2fs (stale)", symptom, elapsed)
            return "STALE_DISCARDED: do not reference this lookup."

        return result_holder["value"]

    @function_tool()
    async def record_patient_note(
        self, context: RunContext, note: str
    ) -> str:
        """Append a short structured note to the patient's record."""
        # In production: write to your actual patient-record store.
        logger.info("Recorded note: %s", note)
        return f"Recorded: {note}"


async def entrypoint(ctx: JobContext) -> None:
    global _guard
    _guard = GenerationGuard()

    await ctx.connect()

    session = AgentSession(
        vad=silero.VAD.load(),
        stt=openai.STT(),
        llm=openai.LLM(model="gpt-4o-mini"),
        # --- Rime is the primary spoken output for this submission ---
        tts=rime.TTS(
            model="mistv2",          # inline pronunciation control — useful
                                       # for medicine names / dosages later
            speaker="abbie",
            lang="eng",
        ),
        # LiveKit's own turn/interruption handling stops audio immediately;
        # our GenerationGuard is the layer on top that fences stale tool
        # results and reconciles conversation state.
        allow_interruptions=True,
    )

    @session.on("user_state_changed")
    def _on_user_state_changed(ev):
        # Fires when the user starts speaking, including mid-agent-turn
        # (barge-in). This is where we bump the generation counter.
        if ev.new_state == "speaking":
            gen = _guard.new_turn()
            logger.info("New turn/interruption detected -> generation %s", gen)

    await session.start(agent=SahaayAssistant(), room=ctx.room)

    await session.generate_reply(
        instructions="Greet the health worker briefly and ask what she needs."
    )


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
