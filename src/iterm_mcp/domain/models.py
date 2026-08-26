"""Small, transport-independent models used by the application layer."""

from dataclasses import dataclass


class TerminalError(Exception):
    """Base class for errors that can be shown to an MCP client."""


class SessionUnavailableError(TerminalError):
    """The requested iTerm2 session does not exist or is unavailable."""


class TerminalOperationError(TerminalError):
    """An iTerm2 operation failed."""


@dataclass(frozen=True, slots=True)
class SessionTarget:
    """A stable target captured at the start of one tool request."""

    session_id: str
    tty_path: str | None = None


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    """Summary returned after a command has been sent."""

    output_lines: int
