"""Smoke tests for the boilerplate.

Designed as a copy-paste seam: when you fork this repo and edit the agent,
these tests should keep passing or you've broken something obvious. They
intentionally don't hit the network or load model weights.
"""

from __future__ import annotations


def test_modules_import() -> None:
    """All source modules import without side effects that need creds."""
    import agent  # noqa: F401
    import avatar_ws  # noqa: F401
    import byo_livekit_demo  # noqa: F401
    import liveavatar_client  # noqa: F401
    import liveavatar_hosted_demo  # noqa: F401
    import pipeline  # noqa: F401
    import worker  # noqa: F401


def test_agent_name_shared_constant() -> None:
    """worker.AGENT_NAME is the single source of truth for dispatch routing.

    If this test fails, the BYO demo will dispatch under a different name
    than the worker registers and jobs will silently never land. Don't
    hardcode the string in two places — import worker.AGENT_NAME.
    """
    import byo_livekit_demo
    import worker

    assert worker.AGENT_NAME == byo_livekit_demo.AGENT_NAME


def test_liveavatar_client_constructs() -> None:
    """LiveAvatarClient builds without making any HTTP calls."""
    from liveavatar_client import LiveAvatarClient

    client = LiveAvatarClient(api_key="test-key", base_url="https://example.invalid")
    assert client._api_key == "test-key"
    assert client._base_url == "https://example.invalid"
