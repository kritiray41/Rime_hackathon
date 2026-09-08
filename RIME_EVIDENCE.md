# RIME Evidence — Sahaay Interruption & Recovery

## 1. Claim

Sahaay is designed to handle user interruptions and corrections safely.

When a health worker interrupts the assistant and provides a correction, the new user turn becomes the current conversational generation.

Work belonging to the previous generation is cancelled where possible and fenced from reaching the conversation state or user.

This prevents stale tool results from being spoken after the user has already corrected themselves.

---

## 2. Generation Guard Design

Each user turn receives a monotonically increasing generation ID.

Example:

- Turn 1 → generation 1
- User interrupts
- Turn 2 → generation 2

Every asynchronous tool operation started for a turn is associated with that generation.

Before a result is delivered, the GenerationGuard checks whether its generation is still current.

If the generation is stale, the result is dropped instead of being delivered to `on_result`.

The guard also records an audit event for:

- `accepted`
- `cancelled`
- `fenced_stale`

The guard additionally rejects work that is started using an already-stale generation.

---

## 3. Acceptance Test

The repeatable acceptance test is:

```bash
python -m pytest test_generation_guard.py -v -s
