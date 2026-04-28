"""LiveKit AgentServer wiring.

Defines the worker process: prewarm hook (loads VAD), the voice pipeline
factory, and the @server.rtc_session entrypoint that runs per job.

The same entrypoint serves two flows:

  * Flow 1 — LiveAvatar hosts the LiveKit room.
    main.py mints a LiveAvatar session, then dispatches a single job to this
    worker via AgentServer.simulate_job(token=...). Run with `python src/main.py`.

  * Flow 2 — we own the LiveKit room.
    Run this module directly (`python src/agent_dispatcher.py dev`) to register
    with LiveKit Cloud and accept dispatched jobs by agent_name.

The LiveAvatar media-server WebSocket URL is passed in via the
`LIVEAVATAR_WS_URL` environment variable so the entrypoint can open the WS
and start forwarding TTS audio to drive the avatar's lip-sync.
"""

from __future__ import annotations

import logging
import os

from dotenv import load_dotenv
from livekit import rtc
from livekit.agents import (
    AgentServer,
    AgentSession,
    JobContext,
    JobProcess,
    cli,
    inference,
    room_io,
)
from livekit.plugins import ai_coustics, silero
from livekit.plugins.turn_detector.multilingual import MultilingualModel

from agent import LiveAvatarAgent
from avatar_ws import AvatarWebSocket

logger = logging.getLogger("agent")

load_dotenv(".env.local")

AGENT_MODEL = "openai/gpt-5.3-chat-latest"


def build_session(vad) -> AgentSession:
    """Construct the voice pipeline: STT → LLM → TTS + VAD + turn detection.

    Plugins pick up a shared aiohttp.ClientSession from the JobContext, which
    is why this must run inside an agent worker process.
    """
    return AgentSession(
        stt=inference.STT(model="deepgram/nova-3", language="multi"),
        llm=inference.LLM(model=AGENT_MODEL),
        tts=inference.TTS(
            model="cartesia/sonic-3", voice="9626c31c-bec5-4cca-baa8-f8ba9e84c8bc"
        ),
        turn_detection=MultilingualModel(),
        vad=vad,
        preemptive_generation=True,
    )


def build_room_options() -> room_io.RoomOptions:
    """Audio input options. We keep audio_output enabled so the AgentSession
    actually runs the TTS pipeline (and our tts_node override fires); the
    raw track it publishes is muted in `local_track_published` below to
    avoid double audio in the room.
    """
    return room_io.RoomOptions(
        audio_input=room_io.AudioInputOptions(
            noise_cancellation=ai_coustics.audio_enhancement(
                model=ai_coustics.EnhancerModel.QUAIL_VF_L
            ),
        ),
    )


# ---- AgentServer (worker) ----------------------------------------------------

server = AgentServer()


def prewarm(proc: JobProcess) -> None:
    """Runs once per worker subprocess before any job is assigned."""
    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


@server.rtc_session(agent_name="my-agent")
async def my_agent(ctx: JobContext) -> None:
    ctx.log_context_fields = {"room": ctx.room.name}

    _wire_observability(ctx)
    _mute_agent_audio_on_publish(ctx)

    # Open the LiveAvatar media-server WebSocket. main.py populates this env
    # var from LiveAvatarClient.start_session() before launching the worker.
    ws_url = os.environ.get("LIVEAVATAR_WS_URL")
    if not ws_url:
        raise RuntimeError(
            "LIVEAVATAR_WS_URL env var not set. main.py must populate it from "
            "LiveAvatarClient.start_session() before launching the worker."
        )
    avatar_ws = AvatarWebSocket(ws_url=ws_url)
    await avatar_ws.connect()
    ctx.add_shutdown_callback(avatar_ws.close)

    session = build_session(ctx.proc.userdata["vad"])
    _wire_session_observability(session)

    await session.start(
        agent=LiveAvatarAgent(avatar_ws=avatar_ws),
        room=ctx.room,
        room_options=build_room_options(),
    )

    await ctx.connect()


def _wire_observability(ctx: JobContext) -> None:
    """Log key room events so it's easy to see what's happening at runtime."""
    room = ctx.room

    @room.on("participant_connected")
    def _on_participant_connected(p):
        logger.info("participant_connected identity=%s kind=%s", p.identity, p.kind)

    @room.on("participant_disconnected")
    def _on_participant_disconnected(p):
        logger.info("participant_disconnected identity=%s", p.identity)

    @room.on("track_subscribed")
    def _on_track_subscribed(track, pub, p):
        logger.info(
            "track_subscribed kind=%s sid=%s from=%s",
            track.kind,
            track.sid,
            p.identity,
        )

    @room.on("disconnected")
    def _on_disconnected(reason=None):
        logger.info("room disconnected reason=%s", reason)


def _wire_session_observability(session: AgentSession) -> None:
    @session.on("user_input_transcribed")
    def _on_user_input(ev):
        logger.info(
            "user_input_transcribed final=%s text=%r",
            getattr(ev, "is_final", None),
            getattr(ev, "transcript", None),
        )

    @session.on("agent_state_changed")
    def _on_agent_state(ev):
        logger.info(
            "agent_state %s -> %s",
            getattr(ev, "old_state", None),
            getattr(ev, "new_state", None),
        )


def _mute_agent_audio_on_publish(ctx: JobContext) -> None:
    """The avatar (driven via WebSocket) publishes the synced voice track.
    Mute the agent's raw TTS track right after it's published so the room
    doesn't carry double audio.
    """

    @ctx.room.on("local_track_published")
    def _on_local_track_published(pub, track):
        if pub.kind == rtc.TrackKind.KIND_AUDIO and isinstance(
            track, rtc.LocalAudioTrack
        ):
            track.mute()
            logger.info("muted local agent audio track sid=%s", pub.sid)


if __name__ == "__main__":
    # Flow 2 entrypoint: register with LiveKit Cloud and accept jobs.
    cli.run_app(server)
