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
            instructions="""You are a helpful voice AI assistant. The user is interacting with you via voice, even if you perceive the conversation as text.
            You eagerly assist users with their questions by providing information from your extensive knowledge.
            Your responses are concise, to the point, and without any complex formatting or punctuation including emojis, asterisks, or other symbols.
            You are curious, friendly, and have a sense of humor.""",
        )
        self._avatar_ws = avatar_ws

    async def tts_node(
        self, text: AsyncIterable[str], model_settings: ModelSettings
    ) -> AsyncIterable[rtc.AudioFrame]:
        """Tee TTS audio: forward each frame to the LiveAvatar media server
        before yielding it to the LiveKit room.
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
