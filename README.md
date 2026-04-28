# LiveAvatar × LiveKit Agents starter (Python)

A minimal end-to-end example of driving a HeyGen [LiveAvatar](https://docs.liveavatar.com)
session with a [LiveKit Agents](https://docs.livekit.io/agents/) Python voice
pipeline. Talk into your browser, the agent thinks, the avatar lip-syncs the
response.

## How it works

There are two integration modes for connecting an agent to LiveAvatar. This
repo implements **Flow 1** end-to-end and leaves the door open for **Flow 2**.

- **Flow 1 — LiveAvatar hosts the LiveKit room.** We call the LiveAvatar API
  to create a session; LiveAvatar provisions a LiveKit room and gives us back
  a `livekit_url`, an agent token, a client (viewer) token, and a media-server
  WebSocket URL. The agent connects to LiveAvatar's room with the agent token,
  generates audio with the LiveKit voice pipeline, and forwards each TTS frame
  over the media-server WebSocket so the avatar lip-syncs to it.
- **Flow 2 — we own the LiveKit room.** We pass our own LiveKit room config to
  LiveAvatar. Useful when you already have a LiveKit project and want
  full control of dispatch / participant management. The same agent
  entrypoint works under LiveKit Cloud worker dispatch (`agent_dispatcher.py`
  is a fully valid `AgentServer` worker — just run it with `cli.run_app`).

## Architecture (Flow 1)

```
┌──────────┐     1. POST /v1/sessions/token     ┌────────────────┐
│          │ ──────────────────────────────────▶│                │
│ main.py  │     2. POST /v1/sessions/start     │ LiveAvatar API │
│          │ ◀──────────────────────────────────│                │
└────┬─────┘   livekit_url, agent_token,        └────────────────┘
     │         client_token, ws_url
     │
     │ 3. AgentServer.simulate_job(token=agent_token)
     ▼
┌────────────────────────┐
│ agent_dispatcher.py    │  LiveKit voice pipeline (STT → LLM → TTS)
│ (LK worker subprocess) │  with `tts_node` teeing audio frames to:
└──────────┬─────────────┘
           │
           ├─ rtc.Room.connect(livekit_url, agent_token)
           │
           └─ websockets.connect(ws_url)
              │  agent.speak / agent.speak_end / agent.interrupt
              ▼
        LiveAvatar media server  ──► avatar lip-syncs ──► LK room

┌──────────────┐
│ viewer/      │  served at http://127.0.0.1:<port>/
│ index.html   │  joins the same LK room w/ client_token,
│ (vanilla JS) │  subscribes to avatar's audio + video
└──────────────┘
```

## Project layout

```
src/
  agent.py              LiveAvatarAgent (instructions + tts_node override)
  agent_dispatcher.py   AgentServer wiring, prewarm, entrypoint
  avatar_ws.py          WebSocket bridge to the LiveAvatar media server
  liveavatar.py         async client for the LiveAvatar HTTP API
  main.py               Flow 1 orchestration + local viewer server
viewer/
  index.html            vanilla-JS LiveKit viewer (auto-connects via query string)
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
(`inference.STT/LLM/TTS`) call LiveKit's hosted inference gateway. The room
you connect to is LiveAvatar's; the inference calls are billed to your LK
project. No LiveKit Cloud worker is registered — the agent runs locally in
`unregistered` devmode.

## Setup

```bash
# 1. Install dependencies (using uv)
uv venv
source .venv/bin/activate
uv pip install -e .

# 2. Download the turn-detector + VAD model weights
python src/agent_dispatcher.py download-files

# 3. Configure environment
cp .env.example .env.local
# fill in: LIVEAVATAR_API_KEY, AVATAR_ID, LIVEKIT_API_KEY, LIVEKIT_API_SECRET
```

## Run

```bash
python src/main.py
```

This will:
1. Mint a LiveAvatar session via the API
2. Start an embedded LiveKit Agents worker (devmode, unregistered)
3. Dispatch a single job into LiveAvatar's room using the pre-minted agent token
4. Open a viewer in your default browser, pre-filled with the room URL +
   client token, that auto-connects and turns on your microphone

Speak. The agent will respond in the avatar's voice with lip-synced video.
Stop with `Ctrl-C`.

### Useful environment variables

| Var | Default | Notes |
|-----|---------|-------|
| `LIVEAVATAR_API_KEY`    | required | LiveAvatar API key |
| `AVATAR_ID`             | required | UUID of your avatar |
| `LIVEAVATAR_BASE_URL`   | `https://api.liveavatar.com` | override for staging |
| `LIVEKIT_API_KEY`       | required | LK Cloud project (for inference gateway) |
| `LIVEKIT_API_SECRET`    | required | LK Cloud project secret |
| `LOG_LEVEL`             | `INFO`   | `DEBUG` to see plugin internals |

## Customizing

- **System prompt / personality** — edit `instructions=` in `LiveAvatarAgent`.
- **STT / LLM / TTS models** — change `inference.STT/LLM/TTS(...)` in
  `agent_dispatcher.build_session`. Available models:
  https://docs.livekit.io/agents/models/
- **Tools (function calling)** — add `@function_tool` methods to
  `LiveAvatarAgent`. See the comment in `agent.py` for an example.
- **Avatar appearance** — change `AVATAR_ID` in `.env.local`.

## Switching to Flow 2 (own the room)

Run the worker directly under LiveKit Cloud dispatch:

```bash
python src/agent_dispatcher.py dev
```

Then trigger jobs by joining a room in your LK project — the worker accepts
dispatches by `agent_name="my-agent"`. You'll also need to mint the
LiveAvatar session yourself and pass `livekit_config` (your room URL +
tokens) into the `create_session_token` request body. The voice pipeline,
WebSocket bridge, and `tts_node` override all stay the same.

