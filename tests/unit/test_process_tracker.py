from __future__ import annotations

import pytest

from iterm_mcp.infrastructure.process_tracker import ProcessTracker


@pytest.mark.asyncio
async def test_active_process_parses_ps_output(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = ProcessTracker()
    monkeypatch.setattr("os.path.exists", lambda _: True)

    async def fake_run(*args: str) -> str:
        if args[1:3] == ("-o", "pgid="):
            return " 123\n"
        assert args[:3] == ("ps", "-t", "ttys001")
        return " 100 100 0.0\n 123 123 0.3\n"

    monkeypatch.setattr(tracker, "_run", fake_run)

    snapshot = await tracker.active_process("/dev/ttys001")

    assert snapshot is not None
    assert snapshot.pid == "123"
    assert snapshot.cpu_percent == 0.3


@pytest.mark.asyncio
async def test_active_process_ignores_idle_shell_outside_foreground_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = ProcessTracker()
    monkeypatch.setattr("os.path.exists", lambda _: True)

    async def fake_run(*args: str) -> str:
        if args[1:3] == ("-o", "pgid="):
            return " 200\n"
        return " 100 100 0.0\n 200 200 99.0\n"

    monkeypatch.setattr(tracker, "_run", fake_run)

    snapshot = await tracker.active_process("/dev/ttys001")

    assert snapshot is not None
    assert snapshot.pid == "200"


@pytest.mark.asyncio
async def test_active_process_returns_none_for_missing_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = ProcessTracker()
    monkeypatch.setattr("os.path.exists", lambda _: False)

    assert await tracker.active_process("/dev/missing") is None
