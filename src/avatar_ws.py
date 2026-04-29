"""Minimal WebSocket bridge to LiveAvatar media server.

Wire protocol: https://docs.liveavatar.com/docs/lite-mode/events

Outgoing (agent → media server):
  - start            : declare audio format
  - agent.speak      : base64 PCM chunk
  - agent.speak_end  : end of utterance
  - agent.interrupt  : cancel current avatar speech
"""

from __future__ import annotations

import asyncio
import audioop
import base64
import json
import logging
import uuid

import websockets
from livekit import rtc
from websockets.exceptions import ConnectionClosed

logger = logging.getLogger("avatar_ws")

# 24kHz mono 16-bit PCM
SAMPLE_RATE = 24000
ONE_SECOND_BYTES = SAMPLE_RATE * 2
FIRST_CHUNK_BYTES = int(ONE_SECOND_BYTES * 0.4)  # 400ms first chunk for low TTFB


class AvatarWebSocket:
    def __init__(self, ws_url: str) -> None:
        self.ws_url = ws_url
        self.ws: websockets.WebSocketClientProtocol | None = None
        self._buffer = bytearray()
        self._is_speaking = False
        self._first_chunk_sent = False
        self._chunk_size = FIRST_CHUNK_BYTES
        self._closed = False  # set by close() — disables auto-reconnect.
        self._reconnect_lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        return self.ws is not None

    async def connect(self) -> None:
        if self.connected:
            return
        # ping_interval=None: disable WS-level keepalive. Continuous audio
        # frames are themselves a liveness signal; some LiveAvatar media
        # servers stall pong responses under load and the client tears down
        # the conn (1011 keepalive timeout).
        self.ws = await websockets.connect(self.ws_url, ping_interval=None)
        logger.info("avatar_ws connected url=%s", self.ws_url)

    async def close(self) -> None:
        self._closed = True
        if self.ws is not None:
            try:
                await self.ws.close()
            except Exception as e:
                logger.warning("avatar_ws close error: %s", e)
            self.ws = None

    async def _reconnect(self) -> None:
        """Drop the dead socket, open a new one. If the agent is mid-speech,
        re-emit the `start` message so the new server-side session knows
        the audio format. In-flight buffered audio is dropped — the next
        send_audio_frame will repopulate.
        """
        async with self._reconnect_lock:
            if self.connected or self._closed:
                return
            logger.info("avatar_ws reconnecting...")
            self.ws = await websockets.connect(self.ws_url, ping_interval=None)
            logger.info("avatar_ws reconnected")
            if self._is_speaking:
                # New server session: replay the start frame. Reset chunking
                # so first chunk after reconnect is small (low TTFB).
                self._first_chunk_sent = False
                self._chunk_size = FIRST_CHUNK_BYTES
                await self.ws.send(
                    json.dumps(
                        {
                            "type": "start",
                            "encoding": "pcm_s16le",
                            "sample_rate": SAMPLE_RATE,
                            "channels": 1,
                        }
                    )
                )

    async def _send_json(self, msg: dict) -> None:
        if self._closed:
            raise RuntimeError("avatar_ws is closed")
        if not self.connected:
            await self.connect()
        assert self.ws is not None
        try:
            await self.ws.send(json.dumps(msg))
        except ConnectionClosed as e:
            logger.warning("avatar_ws send hit ConnectionClosed: %s", e)
            self.ws = None
            await self._reconnect()
            if self.ws is None:
                raise
            await self.ws.send(json.dumps(msg))

    async def start_speaking(self) -> None:
        if self._is_speaking:
            return
        self._first_chunk_sent = False
        self._chunk_size = FIRST_CHUNK_BYTES
        await self._send_json(
            {
                "type": "start",
                "encoding": "pcm_s16le",
                "sample_rate": SAMPLE_RATE,
                "channels": 1,
            }
        )
        self._is_speaking = True
        logger.debug("avatar_ws start_speaking")

    async def send_audio_frame(self, frame: rtc.AudioFrame) -> None:
        if not self._is_speaking:
            await self.start_speaking()

        raw = (
            frame.data.tobytes()
            if hasattr(frame.data, "tobytes")
            else bytes(frame.data)
        )
        if frame.sample_rate != SAMPLE_RATE:
            raw, _ = audioop.ratecv(
                raw, 2, frame.num_channels, frame.sample_rate, SAMPLE_RATE, None
            )
        if frame.num_channels == 2:
            raw = audioop.tomono(raw, 2, 0.5, 0.5)

        self._buffer.extend(raw)
        while len(self._buffer) >= self._chunk_size:
            chunk = bytes(self._buffer[: self._chunk_size])
            del self._buffer[: self._chunk_size]
            b64 = base64.b64encode(chunk).decode("ascii")
            await self._send_json({"type": "agent.speak", "audio": b64})
            if not self._first_chunk_sent:
                self._first_chunk_sent = True
                self._chunk_size = ONE_SECOND_BYTES

    async def finish_speaking(self) -> None:
        if not self._is_speaking:
            return
        if self._buffer:
            b64 = base64.b64encode(bytes(self._buffer)).decode("ascii")
            await self._send_json({"type": "agent.speak", "audio": b64})
            self._buffer.clear()
        await self._send_json({"type": "agent.speak_end"})
        self._is_speaking = False
        logger.debug("avatar_ws finish sending tts data to ws")

    async def interrupt(self) -> None:
        await self._send_json(
            {"type": "agent.interrupt", "event_id": str(uuid.uuid4())}
        )
        self._buffer.clear()
        self._is_speaking = False
        logger.debug("avatar_ws interrupt")
