from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from iterm_mcp.domain.models import SessionTarget, TerminalOperationError
from iterm_mcp.infrastructure import iterm2_adapter
from iterm_mcp.infrastructure.iterm2_adapter import Iterm2Adapter


class UnsupportedPromptMonitor:
    def __init__(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        raise iterm2_adapter.AppVersionTooOld("command end is unavailable")


@pytest.mark.asyncio
async def test_execute_command_sends_once_when_prompt_monitor_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = Iterm2Adapter(completion_timeout=0.01)
    session = SimpleNamespace(async_send_text=AsyncMock())
    monkeypatch.setattr(adapter, "_session", AsyncMock(return_value=session))
    monkeypatch.setattr(iterm2_adapter, "PromptMonitor", UnsupportedPromptMonitor)
    adapter._process_tracker.wait_until_idle = AsyncMock()

    await adapter.execute_command(SessionTarget("session-a", "/dev/ttys001"), "echo ok")

    session.async_send_text.assert_awaited_once_with("echo ok\n")
    adapter._process_tracker.wait_until_idle.assert_awaited_once()


@pytest.mark.asyncio
async def test_execute_command_does_not_hide_send_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = Iterm2Adapter(completion_timeout=0.01)
    session = SimpleNamespace(
        async_send_text=AsyncMock(side_effect=RuntimeError("write failed"))
    )
    monkeypatch.setattr(adapter, "_session", AsyncMock(return_value=session))
    monkeypatch.setattr(iterm2_adapter, "PromptMonitor", UnsupportedPromptMonitor)
    adapter._process_tracker.wait_until_idle = AsyncMock()

    with pytest.raises(TerminalOperationError, match="write failed"):
        await adapter.execute_command(SessionTarget("session-a", "/dev/ttys001"), "echo ok")

    session.async_send_text.assert_awaited_once_with("echo ok\n")
    adapter._process_tracker.wait_until_idle.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_control_uses_session_input_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = Iterm2Adapter()
    session = SimpleNamespace(async_send_text=AsyncMock())
    monkeypatch.setattr(adapter, "_session", AsyncMock(return_value=session))

    await adapter.send_control(SessionTarget("session-a"), 3)

    session.async_send_text.assert_awaited_once_with("\x03")


@pytest.mark.asyncio
async def test_send_control_has_rpc_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = Iterm2Adapter(rpc_timeout=0.01)
    session = SimpleNamespace(async_send_text=AsyncMock())
    monkeypatch.setattr(adapter, "_session", AsyncMock(return_value=session))

    async def never_finishes(_: str) -> None:
        await asyncio.Future()

    session.async_send_text.side_effect = never_finishes

    with pytest.raises(TerminalOperationError, match="Failed to send control character"):
        await adapter.send_control(SessionTarget("session-a"), 3)


def test_content_window_includes_visible_screen_when_request_is_short() -> None:
    info = SimpleNamespace(
        overflow=12,
        first_visible_line_number=20,
        mutable_area_height=36,
    )

    assert Iterm2Adapter._content_window(info, 20) == (20, 36)


def test_content_window_extends_into_scrollback_for_larger_request() -> None:
    info = SimpleNamespace(
        overflow=12,
        first_visible_line_number=40,
        mutable_area_height=36,
    )

    assert Iterm2Adapter._content_window(info, 50) == (26, 50)
