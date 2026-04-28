import logging
import os

from dotenv import load_dotenv
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
    """Voice pipeline. STT → LLM → TTS + VAD + multilingual turn detection.
    Plugins pull a shared http session from JobContext.
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
    return room_io.RoomOptions(
        audio_input=room_io.AudioInputOptions(
            noise_cancellation=ai_coustics.audio_enhancement(
                model=ai_coustics.EnhancerModel.QUAIL_VF_L
            ),
        ),
    )


# ---- AgentServer (worker) ----

server = AgentServer()


def prewarm(proc: JobProcess):
    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


@server.rtc_session(agent_name="my-agent")
async def my_agent(ctx: JobContext):
    """Job entrypoint. Used by:
      - Flow 1: AgentServer.simulate_job(token=...) (pre-minted LiveAvatar token)
      - Flow 2: LK Cloud dispatch (registered worker)
    """
    ctx.log_context_fields = {"room": ctx.room.name}

    # ---------- observability hooks ----------
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

    session = build_session(ctx.proc.userdata["vad"])

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

    # ---------- LiveAvatar media-server WebSocket ----------
    # ws_url passed via env from main.py before server.run().
    ws_url = os.environ.get("LIVEAVATAR_WS_URL")
    if not ws_url:
        raise RuntimeError(
            "LIVEAVATAR_WS_URL env var not set. main.py must populate it from "
            "LiveAvatarClient.start_session() before launching the worker."
        )
    avatar_ws = AvatarWebSocket(ws_url=ws_url)
    await avatar_ws.connect()

    async def _close_avatar_ws():
        await avatar_ws.close()

    ctx.add_shutdown_callback(_close_avatar_ws)

    await session.start(
        agent=LiveAvatarAgent(avatar_ws=avatar_ws),
        room=ctx.room,
        room_options=build_room_options(),
    )

    await ctx.connect()


if __name__ == "__main__":
    cli.run_app(server)
