from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest

from iterm_mcp.application.terminal_service import TerminalService, tail_lines
from iterm_mcp.domain.models import SessionInfo, SessionTarget, SessionUnavailableError


@dataclass
class FakeTerminal:
    targets: list[SessionTarget]
    buffers: list[str]
    resolved: list[str] = field(default_factory=list)
    executed: list[tuple[str, str]] = field(default_factory=list)
    controls: list[tuple[str, int]] = field(default_factory=list)
    explicit_resolved: list[str] = field(default_factory=list)
    session_infos: list[SessionInfo] = field(default_factory=list)

    async def connect(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def resolve_active_session(self) -> SessionTarget:
        target = self.targets.pop(0)
        self.resolved.append(target.session_id)
        return target

    async def resolve_session(self, session_id: str) -> SessionTarget | None:
        self.explicit_resolved.append(session_id)
        return next((target for target in self.targets if target.session_id == session_id), None)

    async def get_active_session(self) -> SessionInfo:
        return next(session for session in self.session_infos if session.is_current_session)

    async def list_sessions(self, include_buried: bool = False) -> list[SessionInfo]:
        return [
            session for session in self.session_infos
            if include_buried or not session.is_buried
        ]

    async def send_text(self, target: SessionTarget, text: str) -> None:
        self.executed.append((target.session_id, text))

    async def execute_command(self, target: SessionTarget, text: str) -> None:
        self.executed.append((target.session_id, text))

    async def read_contents(self, target: SessionTarget, lines: int = 25) -> str:
        del target
        del lines
        return self.buffers.pop(0)

    async def read_line_count(self, target: SessionTarget) -> int:
        del target
        return len(self.buffers.pop(0).split("\n"))

    async def send_control(self, target: SessionTarget, code: int) -> None:
        self.controls.append((target.session_id, code))

    async def wait_for_completion(self, target: SessionTarget) -> None:
        del target


@pytest.mark.asyncio
async def test_execute_pins_one_session_for_before_execute_after_reads() -> None:
    terminal = FakeTerminal(
        targets=[SessionTarget("session-a", "/dev/ttys001")],
        buffers=["prompt", "prompt\nresult"],
    )

    result = await TerminalService(terminal).execute("echo result", session_id="session-a")

    assert result.output_lines == 1
    assert terminal.explicit_resolved == ["session-a"]
    assert terminal.executed == [("session-a", "echo result")]


@pytest.mark.asyncio
async def test_write_sends_without_waiting_or_reading() -> None:
    terminal = FakeTerminal([SessionTarget("session-a")], [])

    result = await TerminalService(terminal).write("python", session_id="session-a")

    assert result.as_dict() == {"session_id": "session-a", "accepted": True}
    assert terminal.executed == [("session-a", "python")]


@pytest.mark.asyncio
async def test_background_operation_has_stable_id_and_session() -> None:
    terminal = FakeTerminal(
        [SessionTarget("session-a")], ["prompt", "prompt\nresult"]
    )

    service = TerminalService(terminal)
    started = await service.start("echo result", session_id="session-a")
    completed = await service.wait(started.operation_id)

    assert completed.operation_id == started.operation_id
    assert completed.session_id == "session-a"
    assert completed.status == "completed"
    assert completed.output_lines == 1


@pytest.mark.asyncio
async def test_concurrent_requests_get_independent_targets() -> None:
    terminal = FakeTerminal(
        targets=[SessionTarget("a"), SessionTarget("b")],
        buffers=["", "a", "", "b"],
    )
    service = TerminalService(terminal)

    await asyncio.gather(
        service.execute("one", session_id="a"),
        service.execute("two", session_id="b"),
    )

    assert sorted(terminal.explicit_resolved) == ["a", "b"]
    assert sorted(target for target, _ in terminal.executed) == ["a", "b"]


@pytest.mark.asyncio
async def test_read_uses_one_target_and_returns_tail() -> None:
    terminal = FakeTerminal(
        targets=[SessionTarget("session-a")],
        buffers=["one\ntwo\nthree"],
    )

    assert await TerminalService(terminal).read(2, session_id="session-a") == "two\nthree"
    assert terminal.explicit_resolved == ["session-a"]


@pytest.mark.asyncio
async def test_send_control_uses_the_resolved_target() -> None:
    terminal = FakeTerminal(
        targets=[SessionTarget("session-a")],
        buffers=[],
    )

    await TerminalService(terminal).send_control(3, session_id="session-a")

    assert terminal.controls == [("session-a", 3)]


@pytest.mark.asyncio
async def test_explicit_session_id_does_not_resolve_active_session() -> None:
    terminal = FakeTerminal(
        targets=[SessionTarget("session-a")],
        buffers=["output"],
    )

    await TerminalService(terminal).read(1, session_id="session-a")

    assert terminal.resolved == []
    assert terminal.explicit_resolved == ["session-a"]


@pytest.mark.asyncio
async def test_missing_explicit_session_does_not_fall_back_to_active() -> None:
    terminal = FakeTerminal(targets=[SessionTarget("active")], buffers=[])

    with pytest.raises(SessionUnavailableError, match="missing"):
        await TerminalService(terminal).read(1, session_id="missing")

    assert terminal.resolved == []


@pytest.mark.asyncio
async def test_blank_explicit_session_id_is_rejected() -> None:
    terminal = FakeTerminal(targets=[SessionTarget("active")], buffers=[])

    with pytest.raises(ValueError, match="non-empty"):
        await TerminalService(terminal).read(1, session_id="  ")


def test_tail_lines_handles_empty_and_non_positive_values() -> None:
    assert tail_lines("", 2) == ""
    assert tail_lines("one\ntwo", 0) == ""
    assert tail_lines("one\ntwo", 1) == "two"
