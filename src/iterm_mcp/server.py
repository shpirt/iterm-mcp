"""FastMCP stdio server and tool definitions."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp.server.fastmcp import FastMCP

from iterm_mcp.application.terminal_service import TerminalService
from iterm_mcp.domain.models import SessionInfoPayload, TerminalError
from iterm_mcp.infrastructure.control_characters import control_code
from iterm_mcp.infrastructure.iterm2_adapter import Iterm2Adapter

logger = logging.getLogger(__name__)


def create_service() -> TerminalService:
    return TerminalService(Iterm2Adapter())


def create_server(service: TerminalService | None = None) -> FastMCP:
    """Create a server instance; injection keeps tool tests independent of iTerm2."""

    terminal_service = service or create_service()

    @asynccontextmanager
    async def lifespan(_server: FastMCP) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await terminal_service.terminal.close()

    mcp = FastMCP("iterm-mcp", lifespan=lifespan)

    def validate_session_id(session_id: str | None) -> str | None:
        if session_id is None:
            return None
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("session_id must be a non-empty string")
        return session_id.strip()

    @mcp.tool()
    async def get_active_session() -> SessionInfoPayload:
        """Return metadata for the currently active iTerm2 session."""

        try:
            return (await terminal_service.get_active_session()).as_dict()
        except TerminalError as exc:
            raise RuntimeError(str(exc)) from exc

    @mcp.tool()
    async def list_sessions(includeBuried: bool = False) -> list[SessionInfoPayload]:
        """List iTerm2 sessions without changing focus or reading terminal contents."""

        if not isinstance(includeBuried, bool):
            raise ValueError("includeBuried must be a boolean")
        try:
            sessions = await terminal_service.list_sessions(includeBuried)
            return [session.as_dict() for session in sessions]
        except TerminalError as exc:
            raise RuntimeError(str(exc)) from exc

    @mcp.tool()
    async def write_to_terminal(command: str, session_id: str | None = None) -> str:
        """Write text or a command to an iTerm2 session."""

        if not isinstance(command, str) or not command.strip():
            raise ValueError("command must be a non-empty string")
        session_id = validate_session_id(session_id)
        try:
            result = await terminal_service.execute(command, session_id)
        except TerminalError as exc:
            raise RuntimeError(str(exc)) from exc
        return (
            f"{result.output_lines} lines were output after sending the command to the terminal. "
            f"Read the last {result.output_lines} lines of terminal contents to orient yourself. "
            "Never assume that the command was executed or that it was successful."
        )

    @mcp.tool()
    async def read_terminal_output(
        linesOfOutput: int = 25,
        session_id: str | None = None,
    ) -> str:
        """Read the requested number of lines from an iTerm2 session."""

        if isinstance(linesOfOutput, bool) or not isinstance(linesOfOutput, int):
            raise ValueError("linesOfOutput must be an integer")
        if linesOfOutput < 1:
            raise ValueError("linesOfOutput must be greater than zero")
        session_id = validate_session_id(session_id)
        try:
            return await terminal_service.read(linesOfOutput, session_id)
        except TerminalError as exc:
            raise RuntimeError(str(exc)) from exc

    @mcp.tool()
    async def send_control_character(
        letter: str,
        session_id: str | None = None,
    ) -> str:
        """Send a control character to an iTerm2 session."""

        if not isinstance(letter, str) or not letter.strip():
            raise ValueError("letter must be a non-empty string")
        session_id = validate_session_id(session_id)
        try:
            normalized = letter.strip().upper()
            await terminal_service.send_control(control_code(normalized), session_id)
        except ValueError:
            raise
        except TerminalError as exc:
            raise RuntimeError(str(exc)) from exc
        return f"Sent control character: Control-{letter.strip().upper()}"

    return mcp


mcp = create_server()


def run() -> None:
    """Run the server over stdio. Logging never writes to stdout."""

    logging.basicConfig(level=logging.INFO)
    mcp.run()
