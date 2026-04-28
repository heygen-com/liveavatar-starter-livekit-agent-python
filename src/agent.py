"""The Agent class. Customize instructions, add tools, and override pipeline
nodes here. The default `tts_node` is overridden to tee TTS audio frames into
the LiveAvatar media-server WebSocket so the avatar lip-syncs to the response.
"""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterable

from livekit import rtc
from livekit.agents import Agent, ModelSettings

from avatar_ws import AvatarWebSocket

logger = logging.getLogger("agent")


class LiveAvatarAgent(Agent):
    def __init__(self, avatar_ws: AvatarWebSocket) -> None:
        super().__init__(
            instructions=(
                "You are a helpful voice AI assistant. The user is interacting "
                "with you via voice, even if you perceive the conversation as "
                "text. You eagerly assist users with their questions by "
                "providing information from your extensive knowledge. Your "
                "responses are concise, to the point, and without any complex "
                "formatting or punctuation including emojis, asterisks, or "
                "other symbols. You are curious, friendly, and have a sense of "
                "humor."
            ),
        )
        self._avatar_ws = avatar_ws

    # To add tools, decorate methods with @function_tool. Example:
    #
    #   from livekit.agents import function_tool, RunContext
    #
    #   @function_tool
    #   async def lookup_weather(self, context: RunContext, location: str):
    #       """Look up current weather for a city. Args: location (str)."""
    #       return "sunny, 70F"

    async def tts_node(
        self, text: AsyncIterable[str], model_settings: ModelSettings
    ) -> AsyncIterable[rtc.AudioFrame]:
        """Override the default TTS node to tee audio frames to the avatar
        media server before yielding them downstream.

        Lifecycle:
          - on each frame: forward to the WS so the avatar lip-syncs
          - on cancel (user interrupt): tell the WS to interrupt the avatar
          - on completion: tell the WS the speech segment ended
        """
        try:
            async for frame in Agent.default.tts_node(self, text, model_settings):
                try:
                    await self._avatar_ws.send_audio_frame(frame)
                except Exception as e:
                    logger.warning("avatar_ws send_audio_frame failed: %s", e)
                yield frame
        except asyncio.CancelledError:
            logger.info("tts_node cancelled (interrupt)")
            try:
                await self._avatar_ws.interrupt()
            except Exception as e:
                logger.warning("avatar_ws interrupt failed: %s", e)
            raise
        finally:
            try:
                await self._avatar_ws.finish_speaking()
            except Exception as e:
                logger.warning("avatar_ws finish_speaking failed: %s", e)
