"""Minimal async client for the LiveAvatar HTTP API.

Docs: https://docs.liveavatar.com/api-reference

Covers the three endpoints needed for Flow (1):
  * POST /v1/sessions/token  — mint a session JWT
  * POST /v1/sessions/start  — get LiveKit + media-server connection info
  * POST /v1/sessions/stop   — end the session
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

DEFAULT_BASE_URL = "https://api.liveavatar.com"


@dataclass
class SessionToken:
    session_id: str
    session_token: str


@dataclass
class StartedSession:
    session_id: str
    livekit_url: str
    livekit_agent_token: str
    livekit_client_token: str
    max_session_duration: int | None = None
    ws_url: str | None = None


class LiveAvatarClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 30.0,
    ):
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=timeout,
            headers={"X-API-KEY": api_key},
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> "LiveAvatarClient":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    async def create_session_token(
        self,
        avatar_id: str,
        *,
        is_sandbox: bool = False,
        max_session_duration: int | None = None,
        video_quality: str = "high",
        video_encoding: str = "H264",
        livekit_config: dict[str, str] | None = None,
    ) -> SessionToken:
        """LITE mode session.

        Without `livekit_config`: LiveAvatar provisions the LiveKit room (Flow 1).
        With `livekit_config` ({livekit_url, livekit_room, livekit_client_token}):
        the avatar joins the caller's room as a participant (Flow 2).
        """
        body: dict[str, Any] = {
            "mode": "LITE",
            "avatar_id": avatar_id,
            "is_sandbox": is_sandbox,
            "video_settings": {
                "quality": video_quality,
                "encoding": video_encoding,
            },
        }
        if max_session_duration is not None:
            body["max_session_duration"] = max_session_duration
        if livekit_config is not None:
            body["livekit_config"] = livekit_config

        resp = await self._http.post("/v1/sessions/token", json=body)
        if resp.is_error:
            raise RuntimeError(
                f"create_session_token failed status={resp.status_code} "
                f"body={resp.text!r} request_body={body!r}"
            )
        data = resp.json()["data"]
        return SessionToken(
            session_id=data["session_id"],
            session_token=data["session_token"],
        )

    async def start_session(self, session_token: str) -> StartedSession:
        """Bearer auth w/ session_token. Returns LiveKit connection info."""
        resp = await self._http.post(
            "/v1/sessions/start",
            headers={"Authorization": f"Bearer {session_token}"},
            json={},
        )
        resp.raise_for_status()
        data = resp.json()["data"]
        return StartedSession(
            session_id=data["session_id"],
            livekit_url=data["livekit_url"],
            livekit_agent_token=data["livekit_agent_token"],
            livekit_client_token=data["livekit_client_token"],
            max_session_duration=data.get("max_session_duration"),
            ws_url=data.get("ws_url"),
        )

    async def stop_session(
        self,
        *,
        session_id: str,
        session_token: str | None = None,
        reason: str = "USER_CLOSED",
    ) -> None:
        """Stop a session.

        Auth modes:
          * API key (default) — uses the X-API-KEY header set on the client.
            Lets a process holding only the API key (e.g. the worker shutdown
            hook) close any session by id.
          * Bearer session_token — pass `session_token` to use the per-session
            JWT instead. Useful when the caller doesn't have the API key.
        """
        headers = (
            {"Authorization": f"Bearer {session_token}"} if session_token else {}
        )
        resp = await self._http.post(
            "/v1/sessions/stop",
            headers=headers,
            json={"session_id": session_id, "reason": reason},
        )
        resp.raise_for_status()
