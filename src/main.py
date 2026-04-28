"""Flow (1) entrypoint.

1. Call LiveAvatar API → mint session → receive LiveKit url + agent_token.
2. Run AgentServer worker locally (unregistered, devmode).
3. Inject the pre-minted LiveAvatar agent_token via simulate_job(token=...).
   Worker connects to LiveAvatar's room w/ that token, full JobContext stack.
"""

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

from agent_dispatcher import server
from liveavatar import LiveAvatarClient

load_dotenv(".env.local")

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("main")


VIEWER_DIR = Path(__file__).resolve().parent.parent / "viewer"


def _serve_viewer() -> tuple[socketserver.TCPServer, int]:
    """Static file server for viewer/ on a free port. Daemon thread."""
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(VIEWER_DIR)
    )

    class _Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *_args):  # silence access logs
            pass

    handler = functools.partial(_Quiet, directory=str(VIEWER_DIR))
    httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, port


def _open_viewer(port: int, livekit_url: str, client_token: str) -> str:
    qs = urllib.parse.urlencode({"url": livekit_url, "token": client_token})
    viewer_url = f"http://127.0.0.1:{port}/?{qs}"
    webbrowser.open(viewer_url)
    return viewer_url


def _decode_jwt_payload(token: str) -> dict:
    """Decode JWT payload w/o signature verification. Used to extract room name."""
    parts = token.split(".")
    if len(parts) < 2:
        raise ValueError("invalid jwt")
    pad = "=" * (-len(parts[1]) % 4)
    return json.loads(base64.urlsafe_b64decode(parts[1] + pad))


async def run() -> None:
    api_key = os.environ["LIVEAVATAR_API_KEY"]
    avatar_id = os.environ["AVATAR_ID"]
    base_url = os.environ.get("LIVEAVATAR_BASE_URL") or "https://api.liveavatar.com"

    # Step 1+2: mint LiveAvatar session
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
        # Viewer joins same room w/ this token.
        logger.info("viewer livekit_client_token=%s", started.livekit_client_token)

    # Spin up viewer + auto-open browser
    httpd, port = _serve_viewer()
    viewer_url = _open_viewer(port, started.livekit_url, started.livekit_client_token)
    logger.info("viewer opened at %s", viewer_url)

    # Extract room name from the agent JWT (LiveAvatar puts it in `video.room`).
    payload = _decode_jwt_payload(started.livekit_agent_token)
    room_name = payload["video"]["room"]
    logger.info("agent_token room=%s identity=%s", room_name, payload.get("sub"))

    # Step 3: configure + run worker. Inject the LiveAvatar agent_token.
    server.update_options(ws_url=started.livekit_url)

    @server.once("worker_started")
    def _dispatch_job() -> None:
        async def _go() -> None:
            await server.simulate_job(
                room=room_name,
                token=started.livekit_agent_token,
                room_info=models.Room(name=room_name, sid="SRM_liveavatar"),
            )
            logger.info("simulate_job dispatched")

        asyncio.create_task(_go())

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    runner = asyncio.create_task(
        server.run(devmode=True, unregistered=True), name="agent-server"
    )
    waiter = asyncio.create_task(stop.wait(), name="stop-waiter")

    try:
        await asyncio.wait(
            {runner, waiter}, return_when=asyncio.FIRST_COMPLETED
        )
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
                logger.warning("runner did not exit, cancelling")
                runner.cancel()
                try:
                    await runner
                except (asyncio.CancelledError, Exception):
                    pass

    httpd.shutdown()

    # Cleanup the LiveAvatar session
    async with LiveAvatarClient(api_key=api_key, base_url=base_url) as la:
        try:
            await la.stop_session(
                token_resp.session_token, session_id=started.session_id
            )
        except Exception as e:
            logger.warning("stop_session failed: %s", e)


if __name__ == "__main__":
    asyncio.run(run())
