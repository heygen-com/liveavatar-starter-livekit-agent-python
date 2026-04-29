# BYO LiveKit demo (Flow 2)

We own the LiveKit room in **our own** LK Cloud project. We mint room
tokens, pass `livekit_config` to the LiveAvatar API so the avatar joins our
room as a participant, then dispatch the agent into the room via
`AgentDispatchService`. The agent worker can run locally in devmode or be
deployed to LK Cloud with `lk agent deploy`.

Entrypoint: [`src/byo_livekit_demo.py`](../src/byo_livekit_demo.py).
Worker: [`src/worker.py`](../src/worker.py).

## When to pick this flow

- You already have a LiveKit Cloud project and want full control of room
  lifecycle, participants, recording, dispatch, etc.
- You want LK Cloud to run the agent worker fleet for you (auto-scale,
  prewarmed processes, rolling deploys via `lk agent deploy`).
- You're ready to ship to production.

## Architecture

```
┌─────────────────────────┐  1. mint room + tokens (LK API)
│ byo_livekit_demo.py     │ ─────────────────────────┐
│                         │                          │
│                         │  2. POST /v1/sessions/   │
│                         │     {token,start} w/     │  ┌────────────────┐
│                         │     livekit_config ────────▶│ LiveAvatar API │
│                         │  3. ws_url back ◀───────────│                │
│                         │                          │  └───────┬────────┘
│                         │  4. AgentDispatchService.│          │
│                         │     create_dispatch ────────────────│ avatar joins
│                         │       agent_name="my-   │          │ our room
│                         │       agent",           │          │
│                         │       metadata={ws_url} │          ▼
└─────────────────────────┘                          ┌──────────────────────┐
                                                     │  Our LiveKit room    │
                                                     │   (our LK project)   │
                                                     └────────┬─────────────┘
                                                              │
                                ┌─────────────────────────────┘
                                ▼
                    ┌────────────────────────┐
                    │ worker.py              │  STT → LLM → TTS
                    │ (LK Cloud worker fleet │  tts_node tees audio to
                    │  via `lk agent deploy` │  avatar media server WS
                    │  or local `dev` mode)  │
                    └────────────────────────┘

┌──────────────┐
│ viewer/      │  served locally at http://127.0.0.1:<port>/
│ index.html   │  joins our LK room w/ viewer_token
│ (vanilla JS) │  subscribes to avatar's audio + video
└──────────────┘
```

## Prerequisites

Complete the [common setup](../README.md#common-setup) first. This demo
needs:

- `LIVEAVATAR_API_KEY`
- `AVATAR_ID`
- `LIVEKIT_URL` (your LK Cloud project's `wss://...livekit.cloud` URL)
- `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET`

> ⚠️ **This demo consumes LiveKit credits on your LK Cloud project** — both
> for the room (rtc minutes) and for STT / LLM / TTS inference.

You also need the LiveKit CLI to deploy the worker:

```bash
brew install livekit-cli
lk cloud auth        # browser login, picks active project
```

## Run (local worker)

For iteration, run the worker locally in devmode and drive dispatches from
your machine. The worker registers against your LK project and accepts
dispatches by `agent_name`.

```bash
# Terminal 1 — start the worker (registers w/ LK Cloud, accepts dispatches)
python src/worker.py dev

# Terminal 2 — drive a session
python src/byo_livekit_demo.py
```

The demo will:

1. Mint a room name + viewer/avatar tokens against your LK project.
2. Call `POST /v1/sessions/token` with `livekit_config={livekit_url,
   livekit_room, livekit_client_token}` so the LiveAvatar avatar joins
   **your** room.
3. Call `POST /v1/sessions/start` to get the media-server `ws_url`.
4. Call `AgentDispatchService.create_dispatch(agent_name="my-agent",
   room=<your room>, metadata={"ws_url": ...})`.
5. Open a viewer in your default browser, pre-filled with `LIVEKIT_URL` +
   viewer token.

Stop with `Ctrl-C`.

## Deploy the worker to LiveKit Cloud

The repo ships with a [`Dockerfile`](../Dockerfile) and
[`livekit.toml`](../livekit.toml) for `lk agent deploy`.

```bash
# First-time setup (writes subdomain + agent id back into livekit.toml)
lk agent create --secrets-file .env.local

# Subsequent updates
lk agent deploy

# Inspect
lk agent status
lk agent logs
```

Notes:

- **Don't bake `LIVEKIT_*` env vars into the image.** LK Cloud injects
  `LIVEKIT_URL` / `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` at runtime.
- `Dockerfile` runs `python src/worker.py download-files` at build time to
  bake the VAD + turn-detector model weights into the image (cold-start
  is fast).
- `livekit.toml` `[agent.id]` is filled in by `lk agent create` on first
  deploy — commit the change.

After deploy, you can drop `python src/worker.py dev` (Terminal 1). Just
run `python src/byo_livekit_demo.py` from anywhere with the right env vars
and dispatches will route to the cloud-managed worker fleet.

## Productionizing the dispatch caller

`byo_livekit_demo.py` mints LK tokens and calls the LiveAvatar API
client-side. **In production, do that on a backend service, not in the
browser**:

1. Authenticate the user.
2. Mint an LK room token (your LK project).
3. Call `LiveAvatarClient.create_session_token` /
   `start_session` with `livekit_config={url, livekit_room, avatar_token}`.
4. `agent_dispatch.create_dispatch(...)` to dispatch the agent.
5. Return `{room_url, viewer_token, ws_url?}` to the browser.

The browser only sees the LK URL and viewer token. No API keys, no
LiveAvatar secrets.
