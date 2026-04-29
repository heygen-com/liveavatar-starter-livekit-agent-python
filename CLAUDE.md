# Claude Code notes

See [README.md](./README.md) for project overview, architecture, and setup.

Two demos, two ops models. See `docs/liveavatar-hosted-demo.md` and
`docs/byo-livekit-demo.md`.

Key files:

- `src/liveavatar_hosted_demo.py` — Flow 1 entrypoint. LiveAvatar provisions
  the LiveKit room in their own LK project. We mint the session, run an
  AgentServer locally (devmode), and dispatch a single job in-process via
  `simulate_job_with_metadata`. Prod path = self-hosted long-lived process
  calling `rtc.Room.connect` directly. **LK Cloud `agent deploy` does NOT
  apply to this flow** (foreign LK project).
- `src/byo_livekit_demo.py` — Flow 2 entrypoint. We own the LiveKit room in
  our own LK Cloud project. Mints tokens, calls LiveAvatar API w/
  livekit_config, and dispatches via `AgentDispatchService`. Prod path =
  `lk agent deploy`.
- `src/worker.py` — LiveKit `AgentServer` worker (was `agent_dispatcher.py`).
  Holds `@server.rtc_session` entrypoint, prewarm hook, observability,
  and the demo-only `simulate_job_with_metadata` helper.
- `src/agent.py` — `LiveAvatarAgent` with a `tts_node` override that tees
  audio to the avatar media server.
- `src/pipeline.py` — shared session/room builders + observability wiring.
- `src/avatar_ws.py` — minimal WebSocket bridge implementing the LiveAvatar
  Lite-Mode events protocol.
- `src/liveavatar_client.py` — async HTTP client for the LiveAvatar API.

Run a demo with `python src/liveavatar_hosted_demo.py` or
`python src/byo_livekit_demo.py`. Format/lint with `uv run ruff format` /
`uv run ruff check`. Tests live under `tests/` (none yet) — run with
`uv run pytest`.
