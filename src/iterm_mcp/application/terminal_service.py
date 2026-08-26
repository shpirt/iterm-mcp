"""Use cases exposed by the MCP server."""

from dataclasses import dataclass

from iterm_mcp.domain.models import (
    ExecutionResult,
    SessionInfo,
    SessionTarget,
    SessionUnavailableError,
)
from iterm_mcp.domain.ports import TerminalPort


def tail_lines(value: str, lines: int) -> str:
    """Return the requested number of logical lines from terminal contents."""

    if lines <= 0:
        return ""
    return "\n".join(value.split("\n")[-lines:])


@dataclass(slots=True)
class TerminalService:
    """Coordinates terminal use cases while pinning one target per request."""

    terminal: TerminalPort

    async def _target(self, session_id: str | None = None) -> SessionTarget:
        # Resolving once is intentional: all operations in this request must use
        # the same session even if the user changes focus while it is running.
        if session_id is None:
            return await self.terminal.resolve_active_session()
        normalized = session_id.strip()
        if not normalized:
            raise ValueError("session_id must be a non-empty string")
        target = await self.terminal.resolve_session(normalized)
        if target is None:
            raise SessionUnavailableError(f"iTerm2 session not found: {normalized}")
        return target

    async def execute(self, command: str, session_id: str | None = None) -> ExecutionResult:
        target = await self._target(session_id)
        before = await self.terminal.read_line_count(target)
        await self.terminal.execute_command(target, command)
        after = await self.terminal.read_line_count(target)
        output_lines = max(0, after - before)
        return ExecutionResult(output_lines=output_lines)

    async def read(self, lines: int, session_id: str | None = None) -> str:
        target = await self._target(session_id)
        return tail_lines(await self.terminal.read_contents(target, lines), lines)

    async def send_control(self, code: int, session_id: str | None = None) -> None:
        target = await self._target(session_id)
        await self.terminal.send_control(target, code)

    async def get_active_session(self) -> SessionInfo:
        return await self.terminal.get_active_session()

    async def list_sessions(self, include_buried: bool = False) -> list[SessionInfo]:
        return await self.terminal.list_sessions(include_buried)
