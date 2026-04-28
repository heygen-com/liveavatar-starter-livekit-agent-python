# Claude Code notes

See [README.md](./README.md) for project overview, architecture, and setup.

Key files for any change:

- `src/main.py` — Flow 1 orchestration: LiveAvatar API → simulate_job → viewer
- `src/agent_dispatcher.py` — LiveKit `AgentServer` worker, voice pipeline,
  observability hooks. Same entrypoint serves Flow 1 (simulate_job) and Flow 2
  (LiveKit Cloud dispatch).
- `src/agent.py` — `LiveAvatarAgent` with a `tts_node` override that tees
  audio to the avatar media server.
- `src/avatar_ws.py` — minimal WebSocket bridge implementing the LiveAvatar
  Lite-Mode events protocol.
- `src/liveavatar.py` — async HTTP client for the LiveAvatar API.

Run with `python src/main.py`. Format/lint with `uv run ruff format` /
`uv run ruff check`. Tests live under `tests/` (none yet) — run with
`uv run pytest`.
