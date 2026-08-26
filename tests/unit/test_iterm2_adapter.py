from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from iterm_mcp.domain.models import SessionTarget, SessionUnavailableError, TerminalOperationError
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
@pytest.mark.parametrize("session_id", ["active", "all", " ACTIVE "])
async def test_resolve_session_rejects_dynamic_proxy_ids(
    monkeypatch: pytest.MonkeyPatch,
    session_id: str,
) -> None:
    adapter = Iterm2Adapter()
    adapter._app = SimpleNamespace(get_session_by_id=Mock())
    monkeypatch.setattr(adapter, "connect", AsyncMock())

    with pytest.raises(SessionUnavailableError, match="reserved"):
        await adapter.resolve_session(session_id)

    adapter._app.get_session_by_id.assert_not_called()


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


def make_session(session_id: str, name: str) -> SimpleNamespace:
    return SimpleNamespace(session_id=session_id, name=name)


@pytest.mark.asyncio
async def test_list_sessions_reports_locations_and_active_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = Iterm2Adapter()
    visible = make_session("session-a", "api")
    buried = make_session("session-b", "buried")
    other = make_session("session-c", "worker")
    tab_one = SimpleNamespace(
        tab_id="tab-1",
        sessions=[visible],
        all_sessions=[visible, buried],
        current_session=visible,
    )
    tab_two = SimpleNamespace(
        tab_id="tab-2",
        sessions=[other],
        all_sessions=[other],
        current_session=other,
    )
    window = SimpleNamespace(
        window_id="window-1",
        tabs=[tab_one, tab_two],
        current_tab=tab_one,
    )
    app = SimpleNamespace(windows=[window], current_window=window)
    adapter._app = app
    monkeypatch.setattr(adapter, "connect", AsyncMock())

    sessions = await adapter.list_sessions()
    all_sessions = await adapter.list_sessions(include_buried=True)

    assert [session.session_id for session in sessions] == ["session-a", "session-c"]
    assert [session.session_id for session in all_sessions] == [
        "session-a",
        "session-b",
        "session-c",
    ]
    assert sessions[0].is_current_session is True
    assert sessions[1].is_current_tab is False
    assert all_sessions[1].is_buried is True
    assert all_sessions[1].tab_index == 0


@pytest.mark.asyncio
async def test_list_sessions_includes_app_level_buried_sessions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = Iterm2Adapter()
    visible = make_session("session-a", "api")
    buried = make_session("session-b", "buried")
    tab = SimpleNamespace(
        tab_id="tab-1",
        sessions=[visible],
        all_sessions=[visible],
        current_session=visible,
    )
    window = SimpleNamespace(
        window_id="window-1",
        tabs=[tab],
        current_tab=tab,
    )
    adapter._app = SimpleNamespace(
        windows=[window],
        current_window=window,
        buried_sessions=[buried],
    )
    monkeypatch.setattr(adapter, "connect", AsyncMock())

    sessions = await adapter.list_sessions(include_buried=True)

    assert [session.session_id for session in sessions] == ["session-a", "session-b"]
    assert sessions[1].is_buried is True
    assert sessions[1].window_id == ""
    assert sessions[1].window_index == -1


@pytest.mark.asyncio
async def test_get_active_session_returns_the_current_session_info(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = Iterm2Adapter()
    session = make_session("session-a", "api")
    tab = SimpleNamespace(
        tab_id="tab-1",
        sessions=[session],
        current_session=session,
    )
    window = SimpleNamespace(
        window_id="window-1",
        tabs=[tab],
        current_tab=tab,
    )
    adapter._app = SimpleNamespace(windows=[window], current_window=window)
    monkeypatch.setattr(adapter, "connect", AsyncMock())

    result = await adapter.get_active_session()

    assert result.session_id == "session-a"
    assert result.name == "api"
    assert result.is_current_window is True
    assert result.is_current_tab is True
    assert result.is_current_session is True


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
