"""Use cases exposed by the MCP server."""

import asyncio
import time
import uuid
from dataclasses import dataclass, field

from iterm_mcp.domain.models import (
    ExecutionResult,
    OperationSnapshot,
    SessionInfo,
    SessionTarget,
    SessionUnavailableError,
    WriteResult,
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
    _operations: dict[str, "_Operation"] = field(default_factory=dict, init=False)
    _session_locks: dict[str, asyncio.Lock] = field(default_factory=dict, init=False)
    max_output_chars: int = 1_000_000
    operation_ttl: float = 600.0

    async def _target(self, session_id: str) -> SessionTarget:
        # Resolving once pins every request to the caller-selected session.
        if not isinstance(session_id, str):
            raise ValueError("session_id must be a non-empty string")
        normalized = session_id.strip()
        if not normalized:
            raise ValueError("session_id must be a non-empty string")
        target = await self.terminal.resolve_session(normalized)
        if target is None:
            raise SessionUnavailableError(f"iTerm2 session not found: {normalized}")
        return target

    async def execute(
        self, command: str, session_id: str, timeout: float = 120.0
    ) -> ExecutionResult:
        operation_id = (await self.start(command, session_id, timeout)).operation_id
        snapshot = await self.wait(operation_id, timeout + 1.0)
        return ExecutionResult(
            session_id=snapshot.session_id,
            exit_code=snapshot.exit_code,
            output=snapshot.output,
            timed_out=snapshot.timed_out,
            truncated=snapshot.truncated,
            duration_ms=snapshot.duration_ms,
            operation_id=operation_id,
            output_lines=snapshot.output_lines or len(snapshot.output.splitlines()),
        )

    async def write(self, text: str, session_id: str) -> WriteResult:
        target = await self._target(session_id)
        await self.terminal.send_text(target, text)
        return WriteResult(session_id=target.session_id)

    async def start(
        self, command: str, session_id: str, timeout: float = 120.0
    ) -> OperationSnapshot:
        self._cleanup_operations()
        target = await self._target(session_id)
        operation_id = uuid.uuid4().hex
        record = _Operation(
            operation_id=operation_id, session_id=target.session_id, timeout=timeout
        )
        self._operations[operation_id] = record
        record.task = asyncio.create_task(self._run_operation(record, target, command))
        return record.snapshot()

    def _cleanup_operations(self) -> None:
        now = time.monotonic()
        expired = [
            operation_id
            for operation_id, record in self._operations.items()
            if record.task is not None
            and record.task.done()
            and record.finished_at
            and now - record.finished_at > self.operation_ttl
        ]
        for operation_id in expired:
            del self._operations[operation_id]

    async def _run_operation(
        self, record: "_Operation", target: SessionTarget, command: str
    ) -> None:
        lock = self._session_locks.setdefault(target.session_id, asyncio.Lock())
        async with lock:
            record.status = "running"
            record.started_at = time.monotonic()
            stop_polling = asyncio.Event()
            poller: asyncio.Task[None] | None = None
            try:
                before = await self.terminal.read_line_count(target)
                poller = asyncio.create_task(
                    self._poll_operation_output(record, target, before, stop_polling)
                )
                try:
                    result = await asyncio.wait_for(
                        self.terminal.execute_command(target, command), record.timeout
                    )
                except TimeoutError:
                    record.timed_out = True
                    record.status = "timed_out"
                    record.duration_ms = int((time.monotonic() - record.started_at) * 1000)
                    record.finished_at = time.monotonic()
                    return
                if result is None:
                    after = await self.terminal.read_line_count(target)
                    record.output = ""
                    record.exit_code = None
                    record.duration_ms = int((time.monotonic() - record.started_at) * 1000)
                    record.output_lines = max(0, after - before)
                else:
                    record.exit_code = result.exit_code
                    record.output = result.output[: self.max_output_chars]
                    record.truncated = (
                        result.truncated or len(result.output) > self.max_output_chars
                    )
                    record.timed_out = result.timed_out
                    record.duration_ms = result.duration_ms
                    record.output_lines = result.output_lines or len(record.output.splitlines())
                if record.status == "cancel_requested" and not record.timed_out:
                    record.status = "cancelled"
                elif record.timed_out:
                    record.status = "timed_out"
                else:
                    record.status = "completed"
                record.finished_at = time.monotonic()
            except asyncio.CancelledError:
                record.status = "cancelled"
                raise
            except Exception as exc:
                record.status = "failed"
                record.error = str(exc)
                record.duration_ms = int((time.monotonic() - record.started_at) * 1000)
                record.finished_at = time.monotonic()
            finally:
                stop_polling.set()
                if poller is not None:
                    await asyncio.gather(poller, return_exceptions=True)

    async def _poll_operation_output(
        self,
        record: "_Operation",
        target: SessionTarget,
        before: int,
        stop: asyncio.Event,
    ) -> None:
        await asyncio.sleep(0.2)
        while not stop.is_set():
            try:
                after = await self.terminal.read_line_count(target)
                count = max(1, min(2000, after - before + 5))
                output = await self.terminal.read_contents(target, count)
                record.output = output[: self.max_output_chars]
                record.truncated = len(output) > self.max_output_chars
                record.output_lines = len(record.output.splitlines())
            except Exception:
                pass
            try:
                await asyncio.wait_for(stop.wait(), 0.2)
            except TimeoutError:
                continue

    def _operation(self, operation_id: str) -> "_Operation":
        record = self._operations.get(operation_id.strip())
        if record is None:
            raise ValueError(f"operation_id not found: {operation_id}")
        return record

    async def read_operation(
        self, operation_id: str, offset: int = 0, max_chars: int = 100_000
    ) -> OperationSnapshot:
        if offset < 0 or max_chars < 1:
            raise ValueError("offset must be non-negative and max_chars must be greater than zero")
        record = self._operation(operation_id)
        snapshot = record.snapshot()
        output = snapshot.output[offset:offset + max_chars]
        return OperationSnapshot(
            operation_id=snapshot.operation_id,
            session_id=snapshot.session_id,
            status=snapshot.status,
            exit_code=snapshot.exit_code,
            output=output,
            next_offset=offset + len(output),
            output_lines=snapshot.output_lines,
            timed_out=snapshot.timed_out,
            truncated=snapshot.truncated,
            duration_ms=snapshot.duration_ms,
            error=snapshot.error,
        )

    async def wait(self, operation_id: str, timeout: float = 30.0) -> OperationSnapshot:
        record = self._operation(operation_id)
        if record.task is not None and record.status in {"queued", "running", "cancel_requested"}:
            try:
                await asyncio.wait_for(asyncio.shield(record.task), timeout)
            except TimeoutError:
                pass
        return record.snapshot()

    async def cancel(self, operation_id: str) -> OperationSnapshot:
        record = self._operation(operation_id)
        if record.status in {"completed", "timed_out", "cancelled", "failed"}:
            return record.snapshot()
        record.status = "cancel_requested"
        await self.send_control(3, record.session_id)
        return record.snapshot()

    async def close(self) -> None:
        tasks = [
            record.task
            for record in self._operations.values()
            if record.task and not record.task.done()
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self.terminal.close()

    async def read(self, lines: int, session_id: str) -> str:
        target = await self._target(session_id)
        return tail_lines(await self.terminal.read_contents(target, lines), lines)

    async def send_control(self, code: int, session_id: str) -> None:
        target = await self._target(session_id)
        await self.terminal.send_control(target, code)

    async def get_active_session(self) -> SessionInfo:
        return await self.terminal.get_active_session()

    async def list_sessions(self, include_buried: bool = False) -> list[SessionInfo]:
        return await self.terminal.list_sessions(include_buried)


@dataclass
class _Operation:
    operation_id: str
    session_id: str
    status: str = "queued"
    exit_code: int | None = None
    output: str = ""
    truncated: bool = False
    timed_out: bool = False
    duration_ms: int = 0
    error: str | None = None
    output_lines: int = 0
    started_at: float = 0.0
    task: asyncio.Task[None] | None = None
    timeout: float = 120.0
    finished_at: float = 0.0

    def snapshot(self) -> OperationSnapshot:
        return OperationSnapshot(
            operation_id=self.operation_id,
            session_id=self.session_id,
            status=self.status,
            exit_code=self.exit_code,
            output=self.output,
            next_offset=len(self.output),
            output_lines=self.output_lines,
            timed_out=self.timed_out,
            truncated=self.truncated,
            duration_ms=self.duration_ms,
            error=self.error,
        )
