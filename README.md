# LiveAvatar × LiveKit Agents starter (Python)

Minimal Python boilerplate for driving a HeyGen
[LiveAvatar](https://docs.liveavatar.com) session with a
[LiveKit Agents](https://docs.livekit.io/agents/) voice pipeline. Talk into
your browser, the agent thinks, the avatar lip-syncs the response.

## Two demos, two ops models

There are two integration modes for connecting a LiveKit agent to LiveAvatar.
They differ in **who owns the LiveKit room**, which in turn determines how
you ship the agent in production.

| Demo | Who owns the LK room | How you ship in prod | Walkthrough |
|------|----------------------|----------------------|-------------|
| **LiveAvatar-hosted** (Flow 1) | LiveAvatar (their LK project) | Self-host a long-lived process (Fly / Render / ECS / k8s) — `lk agent deploy` does **not** apply | [`docs/liveavatar-hosted-demo.md`](./docs/liveavatar-hosted-demo.md) |
| **BYO LiveKit** (Flow 2) | You (your LK Cloud project) | `lk agent deploy` — LK Cloud runs the worker fleet | [`docs/byo-livekit-demo.md`](./docs/byo-livekit-demo.md) |

Both demos share the same agent code (`src/agent.py`, `src/worker.py`,
`src/pipeline.py`, `src/avatar_ws.py`). Only the orchestration entrypoint
and deploy story differ.

## Project layout

```
src/
  liveavatar_hosted_demo.py   Flow 1 entrypoint (LiveAvatar hosts the room)
  byo_livekit_demo.py         Flow 2 entrypoint (you host the room)
  worker.py                   AgentServer worker — used by both demos
  agent.py                    LiveAvatarAgent (instructions + tts_node override)
  pipeline.py                 STT/LLM/TTS session + observability wiring
  avatar_ws.py                WebSocket bridge to the LiveAvatar media server
  liveavatar_client.py        async client for the LiveAvatar HTTP API
viewer/
  index.html                  vanilla-JS LiveKit viewer (auto-connects via query string)
docs/
  liveavatar-hosted-demo.md   Flow 1 walkthrough
  byo-livekit-demo.md         Flow 2 walkthrough
Dockerfile                    LK Cloud agent worker image (Flow 2 deploy)
livekit.toml                  `lk agent` deploy manifest
.env.example
pyproject.toml
```

## Prerequisites

**Accounts**
- LiveAvatar — https://app.liveavatar.com (API key + at least one avatar)
- LiveKit Cloud — https://cloud.livekit.io (any project; used for the
  inference gateway, billed to your project)

**Runtime**
- Python ≥ 3.10 (3.13 tested)
- [`uv`](https://docs.astral.sh/uv/) for dependency management (or `pip`)

**Python dependencies** (declared in `pyproject.toml`):
- `livekit-agents[silero,turn-detector] ~= 1.5` — agent framework, Silero VAD
  plugin, multilingual turn detector
- `livekit-plugins-ai-coustics ~= 0.2` — input noise cancellation
- `httpx` — async HTTP client for the LiveAvatar API
- `websockets` — WebSocket client for the LiveAvatar media-server bridge
- `python-dotenv` — `.env.local` loading
- `audioop-lts` (Python ≥ 3.13 only) — drop-in replacement for the stdlib
  `audioop` module (removed in 3.13), used for resample / mono mixdown

**Why a LiveKit Cloud project?** The voice pipeline plugins
(`inference.STT/LLM/TTS`) call LiveKit's hosted inference gateway.

> ⚠️ **Both demos consume LiveKit credits on _your_ LK Cloud project.**
> Even the LiveAvatar-hosted demo, where LiveAvatar owns the room, routes
> STT / LLM / TTS through your project's inference gateway. Plan accordingly.

## Common setup

```bash
# 1. Install dependencies (using uv)
uv venv
source .venv/bin/activate
uv pip install -e .

# 2. Download the turn-detector + VAD model weights.
#    REQUIRED before either demo. Skipping this will make the agent crash
#    mid-session the first time it tries to detect a turn.
python src/worker.py download-files

# 3. Configure environment
cp .env.example .env.local
# fill in: LIVEAVATAR_API_KEY, LIVEKIT_API_KEY, LIVEKIT_API_SECRET
# (the BYO demo also needs LIVEKIT_URL)
# AVATAR_ID is pre-filled with a public sample — swap once you have your own.
```

### Useful environment variables

| Var | Default | Notes |
|-----|---------|-------|
| `LIVEAVATAR_API_KEY`    | required | LiveAvatar API key |
| `AVATAR_ID`             | required | UUID of your avatar |
| `LIVEAVATAR_BASE_URL`   | `https://api.liveavatar.com` | override for staging |
| `IS_SANDBOX`            | `true`   | sandbox sessions don't burn LiveAvatar credits but are duration-capped. **Remove (or set `false`) in production.** |
| `LIVEKIT_URL`           | required (BYO demo) | wss URL of your LK Cloud project |
| `LIVEKIT_API_KEY`       | required | LK Cloud project (for inference gateway + BYO dispatch) |
| `LIVEKIT_API_SECRET`    | required | LK Cloud project secret |
| `LOG_LEVEL`             | `INFO`   | `DEBUG` to see plugin internals |

## Running the demos

Once the common setup is done, head to the per-demo walkthrough:

- [`docs/liveavatar-hosted-demo.md`](./docs/liveavatar-hosted-demo.md) —
  fastest path; no LK Cloud deploy required, runs entirely on your machine.
- [`docs/byo-livekit-demo.md`](./docs/byo-livekit-demo.md) — production-shaped
  path; deploys the worker to LiveKit Cloud and dispatches into a room you own.

## Customizing

- **System prompt / personality** — edit `instructions=` in `LiveAvatarAgent`
  (`src/agent.py`).
- **STT / LLM / TTS models** — change `inference.STT/LLM/TTS(...)` in
  `pipeline.build_session`. Available models:
  https://docs.livekit.io/agents/models/
- **Tools (function calling)** — add `@function_tool` methods to
  `LiveAvatarAgent`. See the comment in `agent.py` for an example.
- **Avatar appearance** — change `AVATAR_ID` in `.env.local`.
