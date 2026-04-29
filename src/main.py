"""End-to-end entrypoint for Flow (1): LiveAvatar hosts the LiveKit room.

Sequence:
  1. POST /v1/sessions/token   → session_token JWT (LiveAvatarClient)
  2. POST /v1/sessions/start   → livekit_url, livekit_agent_token,
                                 livekit_client_token, ws_url
  3. Run AgentServer locally (devmode, unregistered) and dispatch a single
     job with the pre-minted livekit_agent_token via
     simulate_job_with_metadata, passing ws_url through job metadata.
  4. The worker entrypoint (agent_dispatcher.my_agent) connects to the
     LiveAvatar room, opens the avatar media-server WebSocket, and starts
     the voice pipeline.
  5. A local HTTP server hosts viewer/index.html and the browser auto-opens
     to it w/ url+client_token preloaded so you can talk to the avatar.

Stop with Ctrl-C. The worker drains, the LiveAvatar session is closed, and
the local viewer server shuts down.
"""

from __future__ import annotations

import asyncio
import base64
import functools
import http.server
import json
import logging
import os
import signal
import socketserver
import threading
import urllib.parse
import webbrowser
from pathlib import Path

from dotenv import load_dotenv
from livekit.protocol import models

from agent_dispatcher import server, simulate_job_with_metadata
from liveavatar import LiveAvatarClient

load_dotenv(".env.local")


def _setup_logging() -> None:
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)

    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    root.addHandler(handler)
    root.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())

    for name in ("livekit", "websockets", "httpcore", "httpx"):
        logging.getLogger(name).setLevel(logging.WARNING)


_setup_logging()
logger = logging.getLogger("main")


VIEWER_DIR = Path(__file__).resolve().parent.parent / "viewer"


def _serve_viewer() -> tuple[socketserver.TCPServer, int]:
    class _Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *_args):
            pass

    handler = functools.partial(_Quiet, directory=str(VIEWER_DIR))
    httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, port


def _open_viewer(port: int, livekit_url: str, client_token: str) -> str:
    qs = urllib.parse.urlencode({"url": livekit_url, "token": client_token})
    url = f"http://127.0.0.1:{port}/?{qs}"
    webbrowser.open(url)
    return url


def _decode_jwt_payload(token: str) -> dict:
    parts = token.split(".")
    if len(parts) < 2:
        raise ValueError("invalid jwt")
    pad = "=" * (-len(parts[1]) % 4)
    return json.loads(base64.urlsafe_b64decode(parts[1] + pad))


async def run() -> None:
    api_key = os.environ["LIVEAVATAR_API_KEY"]
    avatar_id = os.environ["AVATAR_ID"]
    base_url = os.environ.get("LIVEAVATAR_BASE_URL") or "https://api.liveavatar.com"

    # 1+2. Mint a LiveAvatar session.
    async with LiveAvatarClient(api_key=api_key, base_url=base_url) as la:
        token_resp = await la.create_session_token(avatar_id=avatar_id)
        logger.info("session_token created session_id=%s", token_resp.session_id)

        started = await la.start_session(token_resp.session_token)
        logger.info(
            "session started livekit_url=%s ws_url=%s session_id=%s",
            started.livekit_url,
            started.ws_url,
            started.session_id,
        )

    if not started.ws_url:
        raise RuntimeError(
            "LiveAvatar start_session did not return ws_url; cannot drive avatar."
        )

    # Viewer
    httpd, port = _serve_viewer()
    viewer_url = _open_viewer(port, started.livekit_url, started.livekit_client_token)
    logger.info("viewer opened: %s", viewer_url)

    # 3. Configure the agent worker.
    payload = _decode_jwt_payload(started.livekit_agent_token)
    room_name = payload["video"]["room"]
    logger.info("agent_token room=%s identity=%s", room_name, payload.get("sub"))

    server.update_options(ws_url=started.livekit_url)

    @server.once("worker_started")
    def _dispatch_job() -> None:
        async def _go() -> None:
            await simulate_job_with_metadata(
                room=room_name,
                token=started.livekit_agent_token,
                metadata=json.dumps({"ws_url": started.ws_url}),
                room_info=models.Room(name=room_name, sid="SRM_liveavatar"),
            )
            logger.info("simulate_job dispatched")

        asyncio.create_task(_go())

    # 4. Run the worker until Ctrl-C / SIGTERM, then shut down gracefully.
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    runner = asyncio.create_task(
        server.run(devmode=True, unregistered=True), name="agent-server"
    )
    waiter = asyncio.create_task(stop.wait(), name="stop-waiter")

    try:
        await asyncio.wait({runner, waiter}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        waiter.cancel()
        if not runner.done():
            logger.info("draining worker...")
            try:
                await server.drain(timeout=10)
            except Exception as e:
                logger.warning("drain error: %s", e)
            try:
                await server.aclose()
            except Exception as e:
                logger.warning("aclose error: %s", e)
            try:
                await asyncio.wait_for(runner, timeout=15)
            except asyncio.TimeoutError:
                runner.cancel()
                try:
                    await runner
                except (asyncio.CancelledError, Exception):
                    pass

        httpd.shutdown()

        async with LiveAvatarClient(api_key=api_key, base_url=base_url) as la:
            try:
                await la.stop_session(
                    token_resp.session_token, session_id=started.session_id
                )
            except Exception as e:
                logger.warning("stop_session failed: %s", e)


if __name__ == "__main__":
    asyncio.run(run())
