# Rime Integration Evidence: Sahaay

## Hard Voice Claim
Our application solves the **Interruption and recovery (full-duplex barge-in)** challenge. When a rural health worker interrupts the voice assistant mid-sentence to change a symptom lookup, obsolete tool results are safely fenced off and discarded so they never re-enter conversation state or get spoken aloud.

## Acceptance Test Procedure
1. Start the agent worker using `python -m src.agent dev`.
2. Connect via the LiveKit Playground and trigger a slow lookup tool: *"Check the protocol for severe fever."*
3. Within the 2-second simulation delay, barge in and change the request: *"Wait, stop, check severe burns instead."*

## Verified Results & Measurement
* **Audio Response:** Rime audio playback stops instantly upon barge-in.
* **Terminal Audit Log:** The application successfully increments generation IDs (`New turn/interruption detected -> generation X`) and explicitly fences stale tool results.
* **Active Speech Provider:** Rime (`mistv2` model, `abbie` speaker, `eng` language) is running as the mandatory primary spoken output.

## Known Limitations
* Requires a stable internet connection; high packet loss can trigger Deepgram STT websocket timeout errors.
