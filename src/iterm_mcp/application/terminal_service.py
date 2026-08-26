"""Use cases exposed by the MCP server."""

from dataclasses import dataclass

from iterm_mcp.domain.models import ExecutionResult, SessionTarget
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

    async def _target(self) -> SessionTarget:
        # Resolving once is intentional: all operations in this request must use
        # the same session even if the user changes focus while it is running.
        return await self.terminal.resolve_active_session()

    async def execute(self, command: str) -> ExecutionResult:
        target = await self._target()
        before = await self.terminal.read_line_count(target)
        await self.terminal.execute_command(target, command)
        after = await self.terminal.read_line_count(target)
        output_lines = max(0, after - before)
        return ExecutionResult(output_lines=output_lines)

    async def read(self, lines: int) -> str:
        target = await self._target()
        return tail_lines(await self.terminal.read_contents(target, lines), lines)

    async def send_control(self, code: int) -> None:
        target = await self._target()
        await self.terminal.send_control(target, code)
