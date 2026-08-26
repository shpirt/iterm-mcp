from __future__ import annotations

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from iterm_mcp.application.terminal_service import TerminalService
from iterm_mcp.domain.models import SessionTarget
from iterm_mcp.server import create_server

from .test_terminal_service import FakeTerminal


@pytest.mark.asyncio
async def test_server_exposes_expected_tools_and_schema() -> None:
    terminal = FakeTerminal([SessionTarget("a")], ["output"])
    server = create_server(TerminalService(terminal))

    tools = await server.list_tools()

    assert {tool.name for tool in tools} == {
        "write_to_terminal",
        "read_terminal_output",
        "send_control_character",
    }
    read_tool = next(tool for tool in tools if tool.name == "read_terminal_output")
    assert read_tool.inputSchema["properties"]["linesOfOutput"]["default"] == 25


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
async def test_server_rejects_invalid_line_count() -> None:
    terminal = FakeTerminal([SessionTarget("a")], ["output"])
    server = create_server(TerminalService(terminal))

    with pytest.raises(ToolError, match="greater than zero"):
        await server.call_tool("read_terminal_output", {"linesOfOutput": 0})
