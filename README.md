# Sahaay: Voice-Native Assistant for Rural Health Workers

Sahaay lets a rural community health worker (ASHA) record and retrieve patient information completely hands-free while attending to patients.

## 🎯 Hard Voice Problem Solved: Interruption & Recovery
Voice assistants often fail when a user interrupts them mid-sentence while a backend tool (like a database lookup or protocol search) is running, leading to stale data being spoken aloud. 
* **Our Solution:** We built a custom `GenerationGuard` module utilizing monotonically increasing generation IDs. When a health worker barges in, obsolete background tasks are instantly fenced off and cancelled, ensuring stale results never re-enter the conversation state.

## Architecture & Third-Party Services
* **Orchestration:** LiveKit Agents (WebRTC)[cite: 1]
* **Speech-to-Text (STT):** Deepgram[cite: 1]
* **LLM / Reasoning:** Groq (mixtral-8x7b-32768)[cite: 1]
* **Text-to-Speech (TTS):** Rime[cite: 1]

## Rime Configuration
* **Model ID:** mistv2[cite: 1]
* **Speaker:** abbie[cite: 1]
* **Language:** eng[cite: 1]
* **Endpoint / Transport:** LiveKit WebRTC Plugin[cite: 1]
* **Audio Format:** PCM (streamed via WebRTC)[cite: 1]

## Setup Instructions
1. Clone the repository and navigate to the project directory.
2. Create a virtual environment: `python3 -m venv venv` and activate it.
3. Install dependencies: `pip install livekit-agents livekit-plugins-rime livekit-plugins-silero livekit-plugins-deepgram livekit-plugins-groq`[cite: 1]
4. Copy `.env.example` to `.env` and add your respective API keys[cite: 1]. 
5. Start the agent: `python -m src.agent dev`[cite: 1]

## Known Limitations & Failure Behavior
* **Network Latency:** The system requires a stable internet connection. High latency will cause turn detection delays or Deepgram API timeouts[cite: 1].
* **Fallback Behavior:** If an in-flight tool call (like a symptom lookup) completes after the user has interrupted the agent to change the topic, the `GenerationGuard` fences the obsolete result so it is never spoken or applied to the new conversation state[cite: 1].
