"""Demo entrypoint — BYO (Bring-Your-Own) LiveKit room (Flow 2).

We own the LiveKit room in our own LK Cloud project. The agent is deployed
to LK Cloud via `lk agent deploy` (or run locally via `python src/worker.py
dev`) and accepts dispatched jobs by agent_name.

Sequence:
  1. Mint room name + viewer/avatar tokens against our LK project.
  2. POST /v1/sessions/token w/ livekit_config so the LiveAvatar avatar joins
     our room using the avatar token we minted.
  3. POST /v1/sessions/start → ws_url for the avatar media server.
  4. Create agent dispatch via livekit.api.AgentDispatchService:
        agent_name="my-agent", room=<our room>, metadata={ws_url}
     LK Cloud routes the dispatch to a registered worker.
  5. Local HTTP server hosts viewer/index.html; browser auto-opens with
     LIVEKIT_URL + viewer_token preloaded.

Stop with Ctrl-C.
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

from liveavatar_client import LiveAvatarClient
from worker import AGENT_NAME

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
logger = logging.getLogger("byo_livekit_demo")


VIEWER_DIR = Path(__file__).resolve().parent.parent / "viewer"
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
    is_sandbox = os.environ.get("IS_SANDBOX", "true").lower() == "true"

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
            is_sandbox=is_sandbox,
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

    # 4. Dispatch agent into our room. Metadata carries ws_url + session_id
    #    so the worker can both drive the avatar and close the LiveAvatar
    #    session in its shutdown callback.
    lkapi = api.LiveKitAPI(url=livekit_url, api_key=lk_key, api_secret=lk_secret)
    try:
        from livekit.protocol.agent_dispatch import CreateAgentDispatchRequest

        dispatch = await lkapi.agent_dispatch.create_dispatch(
            CreateAgentDispatchRequest(
                agent_name=AGENT_NAME,
                room=room_name,
                metadata=json.dumps(
                    {"ws_url": started.ws_url, "session_id": started.session_id}
                ),
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
        # Belt-and-suspenders: worker.my_agent registers its own
        # shutdown callback to stop the LiveAvatar session. In real prod
        # the dispatcher script exits right after create_dispatch, so the
        # worker's callback is the load-bearing one. Kept here so Ctrl-C
        # in the demo guarantees cleanup regardless of worker state.
        async with LiveAvatarClient(api_key=api_key, base_url=base_url) as la:
            try:
                await la.stop_session(session_id=started.session_id)
                logger.info("liveavatar session stopped")
            except Exception as e:
                logger.warning("stop_session failed: %s", e)


if __name__ == "__main__":
    asyncio.run(run())
