"""Small, transport-independent models used by the application layer."""

from dataclasses import dataclass

from typing_extensions import TypedDict


class TerminalError(Exception):
    """Base class for errors that can be shown to an MCP client."""


class SessionUnavailableError(TerminalError):
    """The requested iTerm2 session does not exist or is unavailable."""


class SessionInfoPayload(TypedDict):
    """MCP output shape for session discovery tools."""

    session_id: str
    name: str
    window_id: str
    tab_id: str
    window_index: int
    tab_index: int
    pane_index: int
    is_current_window: bool
    is_current_tab: bool
    is_current_session: bool
    is_buried: bool


class TerminalOperationError(TerminalError):
    """An iTerm2 operation failed."""


@dataclass(frozen=True, slots=True)
class SessionTarget:
    """A stable target captured at the start of one tool request."""

    session_id: str
    tty_path: str | None = None


@dataclass(frozen=True, slots=True)
class SessionInfo:
    """Stable, display-oriented metadata for an iTerm2 session."""

    session_id: str
    name: str
    window_id: str
    tab_id: str
    window_index: int
    tab_index: int
    pane_index: int
    is_current_window: bool
    is_current_tab: bool
    is_current_session: bool
    is_buried: bool

    def as_dict(self) -> SessionInfoPayload:
        """Return the MCP-safe representation without terminal contents or TTY data."""

        return {
            "session_id": self.session_id,
            "name": self.name,
            "window_id": self.window_id,
            "tab_id": self.tab_id,
            "window_index": self.window_index,
            "tab_index": self.tab_index,
            "pane_index": self.pane_index,
            "is_current_window": self.is_current_window,
            "is_current_tab": self.is_current_tab,
            "is_current_session": self.is_current_session,
            "is_buried": self.is_buried,
        }


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    """Summary returned after a command has been sent."""

    output_lines: int
