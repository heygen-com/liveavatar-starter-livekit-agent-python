"""LiveKit AgentServer worker.

Defines the worker process: prewarm hook (loads VAD), the @server.rtc_session
entrypoint, and a small `simulate_job_with_metadata` helper used by the
LiveAvatar-hosted demo.

Both demos run inside an AgentServer worker subprocess and deliver the
LiveAvatar media-server WS URL through job metadata as a JSON blob:
    {"ws_url": "wss://..."}.

  * liveavatar_hosted_demo.py (Flow 1) — LiveAvatar hosts the LiveKit room.
    Mints a LiveAvatar session, then dispatches a single job to this worker
    in-process via simulate_job_with_metadata(token=..., metadata=...).

  * byo_livekit_demo.py (Flow 2) — we own the LiveKit room.
    Run this module directly (`python src/worker.py dev`) or deploy it to
    LK Cloud (`lk agent deploy`) to register and accept dispatched jobs by
    agent_name. The demo drives dispatch via AgentDispatchService.
"""

from __future__ import annotations

import json
import logging

from dotenv import load_dotenv
from livekit import api as lkapi
from livekit.agents import AgentServer, JobContext, JobProcess, cli
from livekit.agents.job import JobAcceptArguments, RunningJobInfo
from livekit.plugins import silero
from livekit.protocol import agent as agent_proto
from livekit.protocol import models

from agent import LiveAvatarAgent
from avatar_ws import AvatarWebSocket
from pipeline import (
    build_room_options,
    build_session,
    mute_agent_audio_on_publish,
    wire_room_observability,
    wire_session_observability,
)

logger = logging.getLogger("agent")

load_dotenv(".env.local")


AGENT_NAME = "my-agent"

server = AgentServer()


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


@server.rtc_session(agent_name=AGENT_NAME)
async def my_agent(ctx: JobContext) -> None:
    ctx.log_context_fields = {"room": ctx.room.name}

    wire_room_observability(ctx.room)
    mute_agent_audio_on_publish(ctx.room)

    raw_meta = (ctx.job.metadata or "").strip()
    if not raw_meta:
        raise RuntimeError("Job metadata missing; expected JSON with ws_url.")
    try:
        meta = json.loads(raw_meta)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Job metadata is not valid JSON: {raw_meta!r}") from e
    ws_url = meta.get("ws_url")
    if not ws_url:
        raise RuntimeError("Job metadata missing 'ws_url'.")

    avatar_ws = AvatarWebSocket(ws_url=ws_url)
    await avatar_ws.connect()
    ctx.add_shutdown_callback(avatar_ws.close)

    session = build_session(ctx.proc.userdata["vad"])
    wire_session_observability(session)

    await session.start(
        agent=LiveAvatarAgent(avatar_ws=avatar_ws),
        room=ctx.room,
        room_options=build_room_options(),
    )

    await ctx.connect()


async def simulate_job_with_metadata(
    *,
    room: str,
    token: str,
    metadata: str,
    room_info: models.Room | None = None,
) -> None:
    """Variant of AgentServer.simulate_job that injects Job.metadata.

    Used by liveavatar_hosted_demo.py so the worker entrypoint can read the
    LiveAvatar ws_url from `ctx.job.metadata` — same code path as real
    AgentDispatchService dispatch.

    Touches a few private AgentServer attrs (`_id`, `_ws_url`, `_proc_pool`).
    Upstream simulate_job builds the Job internally and doesn't expose
    metadata; this helper mirrors its body and adds the field.
    """
    agent_identity = (
        lkapi.TokenVerifier().verify(token, verify_signature=False).identity
    )
    if room_info is None:
        room_info = models.Room(name=room, sid=f"SRM_{room}")

    job = agent_proto.Job(
        id=f"job-sim-{room}",
        room=room_info,
        type=agent_proto.JobType.JT_ROOM,
        metadata=metadata,
    )
    running = RunningJobInfo(
        worker_id=server._id,
        accept_arguments=JobAcceptArguments(
            identity=agent_identity, name="", metadata=""
        ),
        job=job,
        url=server._ws_url,
        token=token,
        fake_job=False,
    )
    await server._proc_pool.launch_job(running)


if __name__ == "__main__":
    cli.run_app(server)
