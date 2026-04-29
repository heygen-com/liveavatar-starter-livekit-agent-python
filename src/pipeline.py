"""Shared voice pipeline + room helpers.

Used by both demos (liveavatar_hosted_demo.py and byo_livekit_demo.py) via
the worker.py entrypoint. Both run inside an AgentServer worker process, so
JobContext.inference_executor is available to plugins like the multilingual
turn detector.
"""

from __future__ import annotations

import logging

from livekit import rtc
from livekit.agents import AgentSession, inference, room_io
from livekit.plugins import ai_coustics
from livekit.plugins.turn_detector.multilingual import MultilingualModel

logger = logging.getLogger("pipeline")

AGENT_MODEL = "openai/gpt-5.3-chat-latest"


def build_session(vad) -> AgentSession:
    """STT → LLM → TTS + VAD + multilingual turn detection."""
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
    """Audio input options. Output is left enabled so AgentSession runs the
    TTS pipeline and the agent's `tts_node` override fires; the raw published
    track is muted via `mute_agent_audio_on_publish` so the room doesn't
    carry double audio (the avatar publishes the lip-synced voice).
    """
    return room_io.RoomOptions(
        audio_input=room_io.AudioInputOptions(
            noise_cancellation=ai_coustics.audio_enhancement(
                model=ai_coustics.EnhancerModel.QUAIL_VF_L
            ),
        ),
    )


def mute_agent_audio_on_publish(room: rtc.Room) -> None:
    @room.on("local_track_published")
    def _on_local_track_published(pub, track):
        if pub.kind == rtc.TrackKind.KIND_AUDIO and isinstance(
            track, rtc.LocalAudioTrack
        ):
            track.mute()
            logger.info("muted local agent audio track sid=%s", pub.sid)


def wire_room_observability(room: rtc.Room) -> None:
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


def wire_session_observability(session: AgentSession) -> None:
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
