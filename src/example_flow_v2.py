"""End-to-end entrypoint for Flow (2): we own the LiveKit room.

Sequence:
  1. Mint room name + viewer/avatar tokens against our LK Cloud project.
  2. POST /v1/sessions/token with livekit_config so the LiveAvatar avatar
     joins our room as a participant (using the avatar token we minted).
  3. POST /v1/sessions/start → ws_url for the avatar media server.
  4. Create an agent dispatch via livekit.api.AgentDispatchService:
        agent_name="my-agent", room=<our room>, metadata={ws_url}
     The worker (registered with LK Cloud via `lk agent deploy` or running
     locally via `python src/agent_dispatcher.py dev`) accepts the dispatch
     and connects to our room.
  5. A local HTTP server hosts viewer/index.html and the browser auto-opens
     to it w/ our LIVEKIT_URL + viewer_token preloaded.

Stop with Ctrl-C. The LiveAvatar session is closed; LK Cloud reaps the
worker job when the room empties.
"""

from __future__ import annotations

import asyncio
import functools
import http.server
import json
import logging
import os
import secrets
import signal
import socketserver
import threading
import urllib.parse
import webbrowser
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv
from livekit import api

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
logger = logging.getLogger("flow_v2")


VIEWER_DIR = Path(__file__).resolve().parent.parent / "viewer"
AGENT_NAME = "my-agent"
AVATAR_IDENTITY = "avatar"
VIEWER_IDENTITY = "viewer"
TOKEN_TTL_SECONDS = 60 * 60  # 1h


def _serve_viewer() -> tuple[socketserver.TCPServer, int]:
    class _Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *_args):
            pass

    handler = functools.partial(_Quiet, directory=str(VIEWER_DIR))
    httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, port


def _open_viewer(port: int, livekit_url: str, viewer_token: str) -> str:
    qs = urllib.parse.urlencode({"url": livekit_url, "token": viewer_token})
    url = f"http://127.0.0.1:{port}/?{qs}"
    webbrowser.open(url)
    return url


def _mint_token(
    api_key: str,
    api_secret: str,
    *,
    identity: str,
    room: str,
    ttl: int = TOKEN_TTL_SECONDS,
) -> str:
    return (
        api.AccessToken(api_key, api_secret)
        .with_identity(identity)
        .with_name(identity)
        .with_ttl(timedelta(seconds=ttl))
        .with_grants(
            api.VideoGrants(
                room_join=True,
                room=room,
                can_publish=True,
                can_subscribe=True,
                can_publish_data=True,
            )
        )
        .to_jwt()
    )


async def run() -> None:
    api_key = os.environ["LIVEAVATAR_API_KEY"]
    avatar_id = os.environ["AVATAR_ID"]
    base_url = os.environ.get("LIVEAVATAR_BASE_URL") or "https://api.liveavatar.com"

    livekit_url = os.environ["LIVEKIT_URL"]
    lk_key = os.environ["LIVEKIT_API_KEY"]
    lk_secret = os.environ["LIVEKIT_API_SECRET"]

    room_name = f"liveavatar-v2-{secrets.token_hex(4)}"
    logger.info("room_name=%s", room_name)

    avatar_token = _mint_token(
        lk_key, lk_secret, identity=AVATAR_IDENTITY, room=room_name
    )
    viewer_token = _mint_token(
        lk_key, lk_secret, identity=VIEWER_IDENTITY, room=room_name
    )

    # 1+2. Mint a LiveAvatar session that joins OUR room.
    async with LiveAvatarClient(api_key=api_key, base_url=base_url) as la:
        token_resp = await la.create_session_token(
            avatar_id=avatar_id,
            livekit_config={
                "livekit_url": livekit_url,
                "livekit_room": room_name,
                "livekit_client_token": avatar_token,
            },
        )
        logger.info("session_token created session_id=%s", token_resp.session_id)

        started = await la.start_session(token_resp.session_token)
        logger.info(
            "session started ws_url=%s session_id=%s",
            started.ws_url,
            started.session_id,
        )

    if not started.ws_url:
        raise RuntimeError(
            "LiveAvatar start_session did not return ws_url; cannot drive avatar."
        )

    # 3. Viewer.
    httpd, port = _serve_viewer()
    viewer_url = _open_viewer(port, livekit_url, viewer_token)
    logger.info("viewer opened: %s", viewer_url)

    # 4. Dispatch agent into our room with ws_url in metadata.
    lkapi = api.LiveKitAPI(url=livekit_url, api_key=lk_key, api_secret=lk_secret)
    try:
        from livekit.protocol.agent_dispatch import CreateAgentDispatchRequest

        dispatch = await lkapi.agent_dispatch.create_dispatch(
            CreateAgentDispatchRequest(
                agent_name=AGENT_NAME,
                room=room_name,
                metadata=json.dumps({"ws_url": started.ws_url}),
            )
        )
        logger.info("dispatch created id=%s", getattr(dispatch, "id", "?"))
    finally:
        await lkapi.aclose()

    # 5. Wait for shutdown signal.
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    logger.info("ready. Ctrl-C to exit.")
    try:
        await stop.wait()
    finally:
        httpd.shutdown()
        async with LiveAvatarClient(api_key=api_key, base_url=base_url) as la:
            try:
                await la.stop_session(
                    token_resp.session_token, session_id=started.session_id
                )
                logger.info("liveavatar session stopped")
            except Exception as e:
                logger.warning("stop_session failed: %s", e)


if __name__ == "__main__":
    asyncio.run(run())
