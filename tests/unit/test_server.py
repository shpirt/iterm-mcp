from __future__ import annotations

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from iterm_mcp.application.terminal_service import TerminalService
from iterm_mcp.domain.models import SessionInfo, SessionTarget
from iterm_mcp.server import create_server

from .test_terminal_service import FakeTerminal


@pytest.mark.asyncio
async def test_server_exposes_expected_tools_and_schema() -> None:
    terminal = FakeTerminal(
        [SessionTarget("a")],
        ["output"],
        session_infos=[
            SessionInfo(
                session_id="a",
                name="zsh",
                window_id="window-1",
                tab_id="tab-1",
                window_index=0,
                tab_index=0,
                pane_index=0,
                is_current_window=True,
                is_current_tab=True,
                is_current_session=True,
                is_buried=False,
            )
        ],
    )
    server = create_server(TerminalService(terminal))

    tools = await server.list_tools()

    assert {tool.name for tool in tools} == {
        "get_active_session",
        "list_sessions",
        "write_to_terminal",
        "execute_command",
        "start_command",
        "read_command_output",
        "wait_command",
        "cancel_command",
        "read_terminal_output",
        "send_control_character",
    }
    read_tool = next(tool for tool in tools if tool.name == "read_terminal_output")
    assert read_tool.inputSchema["properties"]["linesOfOutput"]["default"] == 25
    assert "session_id" in read_tool.inputSchema["properties"]
    assert read_tool.inputSchema["properties"]["session_id"]["anyOf"] == [
        {"type": "string"},
        {"type": "null"},
    ]


@pytest.mark.asyncio
async def test_server_session_discovery_returns_structured_data() -> None:
    info = SessionInfo(
        session_id="a",
        name="zsh",
        window_id="window-1",
        tab_id="tab-1",
        window_index=0,
        tab_index=0,
        pane_index=0,
        is_current_window=True,
        is_current_tab=True,
        is_current_session=True,
        is_buried=False,
    )
    terminal = FakeTerminal([SessionTarget("a")], ["output"], session_infos=[info])
    server = create_server(TerminalService(terminal))

    active_content, active_structured = await server.call_tool("get_active_session", {})
    list_content, list_structured = await server.call_tool("list_sessions", {})

    assert active_content[0].text
    assert active_structured == info.as_dict()
    assert list_content[0].text
    assert list_structured == {"result": [info.as_dict()]}


@pytest.mark.asyncio
async def test_server_call_uses_service_and_returns_text_content() -> None:
    terminal = FakeTerminal([SessionTarget("a")], ["output"])
    server = create_server(TerminalService(terminal))

    content, structured = await server.call_tool(
        "read_terminal_output", {"linesOfOutput": 1}
    )

    assert content[0].text == "output"
    assert structured == {"result": "output"}


@pytest.mark.asyncio
async def test_server_forwards_explicit_session_id() -> None:
    terminal = FakeTerminal([SessionTarget("a")], ["output"])
    server = create_server(TerminalService(terminal))

    await server.call_tool(
        "read_terminal_output",
        {"linesOfOutput": 1, "session_id": "a"},
    )

    assert terminal.resolved == []
    assert terminal.explicit_resolved == ["a"]


@pytest.mark.asyncio
async def test_server_rejects_invalid_line_count() -> None:
    terminal = FakeTerminal([SessionTarget("a")], ["output"])
    server = create_server(TerminalService(terminal))

    with pytest.raises(ToolError, match="greater than zero"):
        await server.call_tool("read_terminal_output", {"linesOfOutput": 0})


@pytest.mark.asyncio
async def test_server_rejects_blank_session_id() -> None:
    terminal = FakeTerminal([SessionTarget("a")], ["output"])
    server = create_server(TerminalService(terminal))

    with pytest.raises(ToolError, match="non-empty"):
        await server.call_tool(
            "read_terminal_output",
            {"linesOfOutput": 1, "session_id": "  "},
        )
