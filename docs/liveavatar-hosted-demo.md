# LiveAvatar-hosted demo (Flow 1)

LiveAvatar provisions the LiveKit room in **their own** LiveKit project. Our
agent connects into that room as a regular client using the agent token
LiveAvatar mints for us. We never touch our own LK project for room
ownership — only for the inference gateway plugins.

Entrypoint: [`src/liveavatar_hosted_demo.py`](../src/liveavatar_hosted_demo.py).

## When to pick this flow

- Quickest possible end-to-end demo.
- You don't have (or don't want to manage) a LiveKit Cloud project for
  rooms.
- You want LiveAvatar to handle room lifecycle, participant management, etc.

## Architecture

```
┌──────────────────────────────┐  1. POST /v1/sessions/token   ┌────────────────┐
│                              │ ─────────────────────────────▶│                │
│ liveavatar_hosted_demo.py    │  2. POST /v1/sessions/start   │ LiveAvatar API │
│                              │ ◀─────────────────────────────│                │
└──────┬───────────────────────┘  livekit_url, agent_token,    └────────────────┘
       │                          client_token, ws_url
       │ 3. simulate_job_with_metadata(token=agent_token)
       ▼
┌────────────────────────┐
│ worker.py              │  LiveKit voice pipeline (STT → LLM → TTS)
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

## Prerequisites

Complete the [common setup](../README.md#common-setup) first. This demo
needs:

- `LIVEAVATAR_API_KEY`
- `AVATAR_ID`
- `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` (used by the inference gateway
  plugins — billed to your LK Cloud project, even though the **room** is
  LiveAvatar's)

`LIVEKIT_URL` is **not** used by this demo. The room URL comes back from
the LiveAvatar API.

> ⚠️ **Inference is billed to your LK Cloud project.** The room belongs to
> LiveAvatar, but every STT / LLM / TTS call still routes through your
> project's inference gateway and consumes LiveKit credits.

## Run

```bash
python src/liveavatar_hosted_demo.py
```

This will:
1. Mint a LiveAvatar session via the API.
2. Start an embedded LiveKit Agents worker locally (devmode, unregistered).
3. Dispatch a single job in-process using the pre-minted agent token.
4. Open a viewer in your default browser, pre-filled with the room URL +
   client token, that auto-connects and turns on your microphone.

Speak. The agent will respond in the avatar's voice with lip-synced video.
Stop with `Ctrl-C`.

## How `simulate_job_with_metadata` works

`simulate_job_with_metadata` (in `src/worker.py`) is a thin variant of
`AgentServer.simulate_job` that injects `Job.metadata` so we can pass the
LiveAvatar media-server WS URL through to the worker entrypoint. It
reuses the same `@server.rtc_session` handler that the BYO demo uses, so
the worker code path is identical to a real production dispatch.

It touches a few private `AgentServer` attrs (`_id`, `_ws_url`,
`_proc_pool`) to build the `RunningJobInfo` directly. That's fine for a
demo, but **don't ship this to production unmodified** — see below.

## Productionizing this flow

`lk agent deploy` does **not** work for this flow. LK Cloud workers
register against your LK project; LiveAvatar's room lives in their LK
project. Foreign project = no dispatch.

Instead, ship a long-lived python process (Fly / Render / ECS / k8s) that:

1. Calls the LiveAvatar API to mint a session.
2. Calls `rtc.Room.connect(livekit_url, agent_token)` directly.
3. Starts an `AgentSession` against that connected room.
4. Opens the WS bridge to the media server.

You can drop `AgentServer` and `simulate_job_with_metadata` entirely —
`AgentSession.start(room=connected_room, ...)` is all you need. Use the
existing `pipeline.build_session` / `build_room_options` /
`mute_agent_audio_on_publish` / `wire_*_observability` helpers as-is.

If you'd rather scale via dispatch, switch to the
[BYO LiveKit demo](./byo-livekit-demo.md).
