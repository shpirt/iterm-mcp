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
            await terminal_service.close()

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
    async def write_to_terminal(command: str, session_id: str | None = None) -> dict[str, object]:
        """Send text immediately; use for REPLs, interactive SSH, top, exit, and exec.

        This tool does not wait for completion or report a command exit code.
        Pass the returned session_id to later reads; do not infer it from the active tab.
        """

        if not isinstance(command, str) or not command.strip():
            raise ValueError("command must be a non-empty string")
        session_id = validate_session_id(session_id)
        try:
            result = await terminal_service.write(command, session_id)
        except TerminalError as exc:
            raise RuntimeError(str(exc)) from exc
        return result.as_dict()

    @mcp.tool()
    async def execute_command(
        command: str, session_id: str | None = None, timeout_seconds: float = 120.0
    ) -> dict[str, object]:
        """Run a command expected to return and return its exit code and output.

        Do not use for interactive SSH, REPLs, top, exit, or exec; use write_to_terminal.
        Always pass session_id when operating across multiple requests or tabs.
        """
        if not isinstance(command, str) or not command.strip():
            raise ValueError("command must be a non-empty string")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be greater than zero")
        session_id = validate_session_id(session_id)
        try:
            result = await terminal_service.execute(command, session_id, timeout_seconds)
        except TerminalError as exc:
            raise RuntimeError(str(exc)) from exc
        return result.as_dict()

    @mcp.tool()
    async def start_command(
        command: str, session_id: str | None = None, timeout_seconds: float = 120.0
    ) -> dict[str, object]:
        """Start a returning command in the background and return operation_id immediately.

        Use read_command_output or wait_command to follow it. Use write_to_terminal for REPLs.
        """
        if not isinstance(command, str) or not command.strip():
            raise ValueError("command must be a non-empty string")
        session_id = validate_session_id(session_id)
        try:
            if (
                isinstance(timeout_seconds, bool)
                or not isinstance(timeout_seconds, (int, float))
                or timeout_seconds <= 0
            ):
                raise ValueError("timeout_seconds must be greater than zero")
            return (await terminal_service.start(command, session_id, timeout_seconds)).as_dict()
        except TerminalError as exc:
            raise RuntimeError(str(exc)) from exc

    @mcp.tool()
    async def read_command_output(
        operation_id: str, offset: int = 0, max_chars: int = 100_000
    ) -> dict[str, object]:
        """Read output and status for an operation without relying on the active session."""
        if not isinstance(operation_id, str) or not operation_id.strip():
            raise ValueError("operation_id must be a non-empty string")
        try:
            return (
                await terminal_service.read_operation(operation_id, offset, max_chars)
            ).as_dict()
        except TerminalError as exc:
            raise RuntimeError(str(exc)) from exc

    @mcp.tool()
    async def wait_command(operation_id: str, timeout_seconds: float = 30.0) -> dict[str, object]:
        """Wait for one operation; wait timeout is separate from command timed_out status."""
        if not isinstance(operation_id, str) or not operation_id.strip():
            raise ValueError("operation_id must be a non-empty string")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be greater than zero")
        try:
            return (await terminal_service.wait(operation_id, timeout_seconds)).as_dict()
        except TerminalError as exc:
            raise RuntimeError(str(exc)) from exc

    @mcp.tool()
    async def cancel_command(operation_id: str) -> dict[str, object]:
        """Request best-effort Ctrl-C cancellation for a background operation."""
        if not isinstance(operation_id, str) or not operation_id.strip():
            raise ValueError("operation_id must be a non-empty string")
        try:
            return (await terminal_service.cancel(operation_id)).as_dict()
        except TerminalError as exc:
            raise RuntimeError(str(exc)) from exc

    @mcp.tool()
    async def read_terminal_output(
        linesOfOutput: int = 25,
        session_id: str | None = None,
    ) -> str:
        """Read a specific session; pass session_id after any cross-request tab change."""

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
