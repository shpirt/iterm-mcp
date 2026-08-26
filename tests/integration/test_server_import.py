import asyncio
import os
import uuid

import pytest


@pytest.mark.skipif(
    os.environ.get("ITERM_MCP_INTEGRATION") != "1",
    reason="requires a running iTerm2 instance with Python API enabled",
)
def test_integration_environment_is_explicitly_enabled() -> None:
    from iterm_mcp.infrastructure.iterm2_adapter import Iterm2Adapter

    assert Iterm2Adapter is not None


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(
    os.environ.get("ITERM_MCP_INTEGRATION") != "1",
    reason="requires a running iTerm2 instance with Python API enabled",
)
async def test_two_sessions_execute_and_read_bounded_output() -> None:
    from iterm2.app import async_get_app

    from iterm_mcp.infrastructure.iterm2_adapter import Iterm2Adapter

    adapter = Iterm2Adapter(completion_timeout=10, rpc_timeout=5)
    try:
        await asyncio.wait_for(adapter.connect(), timeout=10)
        assert adapter._connection is not None
        app = await async_get_app(adapter._connection)
        window = app.current_window if app else None
        assert window is not None
        sessions = [tab.current_session for tab in window.tabs]
        sessions = [session for session in sessions if session is not None]
        assert len(sessions) >= 2, "at least two iTerm2 tabs with sessions are required"

        for index, session in enumerate(sessions[:2]):
            target = await asyncio.wait_for(
                adapter.resolve_session(session.session_id), timeout=5
            )
            assert target is not None
            marker = f"iterm-mcp-integration-{index}-{uuid.uuid4().hex}"
            await asyncio.wait_for(
                adapter.execute_command(target, f"printf '{marker}\\n'"), timeout=20
            )
            output = await asyncio.wait_for(
                adapter.read_contents(target, lines=20), timeout=10
            )
            assert marker in output
    finally:
        await asyncio.wait_for(adapter.close(), timeout=5)
