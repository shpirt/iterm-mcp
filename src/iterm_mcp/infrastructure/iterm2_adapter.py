"""Adapter for iTerm2's long-lived Python API connection."""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from contextlib import suppress
from typing import Any, cast

from iterm2.app import async_get_app
from iterm2.capabilities import AppVersionTooOld
from iterm2.connection import Connection
from iterm2.prompt import PromptMonitor
from iterm2.transaction import Transaction

from iterm_mcp.domain.models import (
    ExecutionResult,
    SessionInfo,
    SessionTarget,
    SessionUnavailableError,
    TerminalOperationError,
)

from .output_reader import normalize_lines
from .process_tracker import ProcessTracker

logger = logging.getLogger(__name__)


class Iterm2Adapter:
    """Implements terminal operations against one iTerm2 App connection."""

    _PROXY_SESSION_IDS = frozenset({"active", "all"})

    def __init__(
        self,
        completion_timeout: float = 120.0,
        rpc_timeout: float = 15.0,
    ) -> None:
        self._connection: Connection | None = None
        self._app: Any | None = None
        self._connect_lock = asyncio.Lock()
        self._completion_timeout = completion_timeout
        self._rpc_timeout = rpc_timeout
        self._process_tracker = ProcessTracker()
        self._session_send_locks: dict[str, asyncio.Lock] = {}

    async def connect(self) -> None:
        if self._connection is not None and self._app is not None:
            return
        async with self._connect_lock:
            try:
                if self._connection is None:
                    self._connection = await asyncio.wait_for(
                        Connection.async_create(), self._rpc_timeout
                    )
                self._app = await asyncio.wait_for(
                    async_get_app(self._connection), self._rpc_timeout
                )
                if self._app is None:
                    raise TerminalOperationError("Unable to access the iTerm2 application")
            except TerminalOperationError:
                raise
            except Exception as exc:
                await self._close_connection(self._connection)
                self._connection = None
                self._app = None
                raise TerminalOperationError(f"Failed to connect to iTerm2: {exc}") from exc

    async def close(self) -> None:
        connection = self._connection
        self._connection = None
        self._app = None
        await self._close_connection(connection)

    async def _close_connection(self, connection: Connection | None) -> None:
        if connection is None:
            return
        dispatch_task = getattr(connection, "_Connection__dispatch_forever_future", None)
        if dispatch_task is not None and not dispatch_task.done():
            dispatch_task.cancel()
            with suppress(asyncio.CancelledError):
                await dispatch_task
        websocket = connection.websocket
        if websocket is not None:
            with suppress(Exception):
                await asyncio.wait_for(websocket.close(), self._rpc_timeout)

    def _require_app(self) -> Any:
        if self._app is None:
            raise TerminalOperationError("The iTerm2 API connection is not initialized")
        return self._app

    async def _target_for_session(self, session: Any) -> SessionTarget:
        tty_path: str | None = None
        try:
            value = await asyncio.wait_for(
                session.async_get_variable("tty"), self._rpc_timeout
            )
            tty_path = str(value) if value else None
        except Exception:  # TTY is only needed by the fallback detector.
            logger.debug("Could not read TTY for session %s", session.session_id, exc_info=True)
        return SessionTarget(session_id=session.session_id, tty_path=tty_path)

    async def resolve_active_session(self) -> SessionTarget:
        try:
            await self.connect()
            app = self._require_app()
            window = app.current_window
            session = window.current_tab.current_session if window and window.current_tab else None
            if session is None:
                raise SessionUnavailableError("No active iTerm2 session is available")
            return await self._target_for_session(session)
        except (SessionUnavailableError, TerminalOperationError):
            raise
        except Exception as exc:
            raise TerminalOperationError(
                f"Failed to resolve the active iTerm2 session: {exc}"
            ) from exc

    async def resolve_session(self, session_id: str) -> SessionTarget | None:
        try:
            await self.connect()
            normalized = session_id.strip()
            if not normalized:
                return None
            if normalized.lower() in self._PROXY_SESSION_IDS:
                raise SessionUnavailableError(
                    f"iTerm2 session ID is reserved and not a concrete session: {normalized}"
                )
            session = self._require_app().get_session_by_id(normalized, include_buried=True)
            if session is not None and str(session.session_id) != normalized:
                raise SessionUnavailableError(
                    f"iTerm2 session ID did not resolve to the requested session: {normalized}"
                )
            return await self._target_for_session(session) if session else None
        except (SessionUnavailableError, TerminalOperationError):
            raise
        except Exception as exc:
            raise TerminalOperationError(
                f"Failed to resolve iTerm2 session {session_id}: {exc}"
            ) from exc

    @staticmethod
    def _session_info(
        session: Any,
        window: Any,
        tab: Any,
        window_index: int,
        tab_index: int,
        pane_index: int,
        current_window_id: str | None,
        current_tab_id: str | None,
        current_session_id: str | None,
        is_buried: bool,
    ) -> SessionInfo:
        return SessionInfo(
            session_id=str(session.session_id),
            name=str(getattr(session, "name", "")),
            window_id=str(window.window_id),
            tab_id=str(tab.tab_id),
            window_index=window_index,
            tab_index=tab_index,
            pane_index=pane_index,
            is_current_window=str(window.window_id) == current_window_id,
            is_current_tab=str(tab.tab_id) == current_tab_id,
            is_current_session=str(session.session_id) == current_session_id,
            is_buried=is_buried,
        )

    async def get_active_session(self) -> SessionInfo:
        try:
            await self.connect()
            app = self._require_app()
            window = app.current_window
            tab = window.current_tab if window else None
            session = tab.current_session if tab else None
            if window is None or tab is None or session is None:
                raise SessionUnavailableError("No active iTerm2 session is available")
            window_index = next(
                (index for index, candidate in enumerate(app.windows)
                 if candidate.window_id == window.window_id),
                0,
            )
            tab_index = next(
                (index for index, candidate in enumerate(window.tabs)
                 if candidate.tab_id == tab.tab_id),
                0,
            )
            pane_index = next(
                (index for index, candidate in enumerate(tab.sessions)
                 if candidate.session_id == session.session_id),
                0,
            )
            return self._session_info(
                session,
                window,
                tab,
                window_index,
                tab_index,
                pane_index,
                str(window.window_id),
                str(tab.tab_id),
                str(session.session_id),
                False,
            )
        except (SessionUnavailableError, TerminalOperationError):
            raise
        except Exception as exc:
            raise TerminalOperationError(
                f"Failed to resolve the active iTerm2 session: {exc}"
            ) from exc

    async def list_sessions(self, include_buried: bool = False) -> list[SessionInfo]:
        try:
            await self.connect()
            app = self._require_app()
            current_window = app.current_window
            current_tab = current_window.current_tab if current_window else None
            current_session = current_tab.current_session if current_tab else None
            current_window_id = str(current_window.window_id) if current_window else None
            current_tab_id = str(current_tab.tab_id) if current_tab else None
            current_session_id = str(current_session.session_id) if current_session else None

            result: list[SessionInfo] = []
            seen_session_ids: set[str] = set()
            for window_index, window in enumerate(app.windows):
                for tab_index, tab in enumerate(window.tabs):
                    visible_sessions = {str(session.session_id) for session in tab.sessions}
                    sessions = tab.all_sessions if include_buried else tab.sessions
                    for pane_index, session in enumerate(sessions):
                        session_id = str(session.session_id)
                        seen_session_ids.add(session_id)
                        result.append(
                            self._session_info(
                                session,
                                window,
                                tab,
                                window_index,
                                tab_index,
                                pane_index,
                                current_window_id,
                                current_tab_id,
                                current_session_id,
                                session_id not in visible_sessions,
                            )
                        )
            if include_buried:
                for session in getattr(app, "buried_sessions", []):
                    session_id = str(session.session_id)
                    if session_id in seen_session_ids:
                        continue
                    result.append(
                        SessionInfo(
                            session_id=session_id,
                            name=str(getattr(session, "name", "")),
                            window_id="",
                            tab_id="",
                            window_index=-1,
                            tab_index=-1,
                            pane_index=-1,
                            is_current_window=False,
                            is_current_tab=False,
                            is_current_session=False,
                            is_buried=True,
                        )
                    )
            return result
        except TerminalOperationError:
            raise
        except Exception as exc:
            raise TerminalOperationError(f"Failed to list iTerm2 sessions: {exc}") from exc

    async def _session(self, target: SessionTarget) -> Any:
        await self.connect()
        session = self._require_app().get_session_by_id(target.session_id, include_buried=True)
        if session is None:
            raise SessionUnavailableError(f"iTerm2 session not found: {target.session_id}")
        return session

    async def _send_text_to_session(
        self, session: Any, text: str, session_id: str | None = None
    ) -> None:
        try:
            lock = self._session_send_locks.setdefault(
                str(session_id or getattr(session, "session_id", "default")), asyncio.Lock()
            )
            async with lock:
                await asyncio.wait_for(session.async_send_text(text + "\n"), self._rpc_timeout)
        except Exception as exc:
            raise TerminalOperationError(f"Failed to send text: {exc}") from exc

    async def send_text(self, target: SessionTarget, text: str) -> None:
        await self._send_text_to_session(await self._session(target), text, target.session_id)

    async def execute_command(self, target: SessionTarget, text: str) -> ExecutionResult:
        """Execute a returning command using Shell Integration or a sentinel fallback."""

        session = await self._session(target)
        started = time.monotonic()
        sent = False
        try:
            before = await self.read_line_count(target)
        except TerminalOperationError:
            before = 0
        monitor: Any | None = None
        try:
            assert self._connection is not None
            monitor = PromptMonitor(
                self._connection, target.session_id,
                modes=[PromptMonitor.Mode.COMMAND_END],
            )
            await asyncio.wait_for(monitor.__aenter__(), self._rpc_timeout)  # type: ignore[no-untyped-call]
            await self._send_text_to_session(session, text, target.session_id)
            sent = True
            event = await asyncio.wait_for(
                monitor.async_get(), self._completion_timeout
            )
            exit_code = event[1]
            output = await self._output_since(target, before)
            return ExecutionResult(
                session_id=target.session_id, exit_code=int(exit_code), output=output,
                output_lines=max(0, len(output.splitlines())),
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        except AppVersionTooOld:
            logger.debug("Prompt monitor unavailable; using sentinel", exc_info=True)
        except TimeoutError:
            if monitor is not None:
                output = await self._output_since(target, before)
                return ExecutionResult(
                    session_id=target.session_id, output=output, timed_out=True,
                    output_lines=max(0, len(output.splitlines())),
                    duration_ms=int((time.monotonic() - started) * 1000),
                )
        except (SessionUnavailableError, TerminalOperationError):
            raise
        except Exception:
            if sent:
                return ExecutionResult(
                    session_id=target.session_id,
                    timed_out=True,
                    duration_ms=int((time.monotonic() - started) * 1000),
                )
            logger.debug("Prompt monitor failed; using sentinel", exc_info=True)
        finally:
            if monitor is not None:
                with suppress(Exception):
                    await asyncio.wait_for(monitor.__aexit__(None, None, None), self._rpc_timeout)

        # Compatibility for isolated callers/tests that provide no API connection.
        # A live adapter always has a connection and uses the sentinel path above.
        if self._connection is None and target.tty_path:
            await self._send_text_to_session(session, text, target.session_id)
            await self._process_tracker.wait_until_idle(target.tty_path, self._completion_timeout)
            return ExecutionResult(session_id=target.session_id)
        return await self._execute_with_sentinel(target, text, before, started)

    async def _output_since(self, target: SessionTarget, before: int) -> str:
        after = await self.read_line_count(target)
        count = max(1, min(2000, after - before + 5))
        return await self.read_contents(target, count)

    async def _execute_with_sentinel(
        self, target: SessionTarget, text: str, before: int, started: float
    ) -> ExecutionResult:
        token = f"__ITERM_MCP_EXIT_{uuid.uuid4().hex}__"
        wrapped = f"{{\n{text}\n}}; __iterm_mcp_rc=$?; printf '\\n{token}%s\\n' \"$__iterm_mcp_rc\""
        await self._send_text_to_session(await self._session(target), wrapped, target.session_id)
        pattern = re.compile(re.escape(token) + r"(-?\d+)")
        deadline = time.monotonic() + self._completion_timeout
        output = ""
        while time.monotonic() < deadline:
            output = await self._output_since(target, before)
            match = pattern.search(output)
            if match:
                output = output[:match.start()].rstrip("\n")
                return ExecutionResult(
                    session_id=target.session_id, exit_code=int(match.group(1)), output=output,
                    output_lines=len(output.splitlines()),
                    duration_ms=int((time.monotonic() - started) * 1000),
                )
            await asyncio.sleep(0.2)
        return ExecutionResult(
            session_id=target.session_id, output=output, timed_out=True,
            output_lines=len(output.splitlines()),
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    async def read_contents(self, target: SessionTarget, lines: int = 25) -> str:
        try:
            session = await self._session(target)
            assert self._connection is not None
            content_lines = await asyncio.wait_for(
                self._read_contents_rpc(session, lines), self._rpc_timeout
            )
            return normalize_lines(content_lines)
        except SessionUnavailableError:
            raise
        except Exception as exc:
            raise TerminalOperationError(f"Failed to read terminal output: {exc}") from exc

    async def _read_contents_rpc(self, session: Any, requested_lines: int) -> list[Any]:
        assert self._connection is not None
        async with Transaction(self._connection):
            info = await session.async_get_line_info()
            first_line, count = self._content_window(info, requested_lines)
            if count <= 0:
                return []
            return cast(list[Any], await session.async_get_contents(first_line, count))

    @staticmethod
    def _content_window(info: Any, requested_lines: int) -> tuple[int, int]:
        """Choose a bounded range that includes the currently visible screen.

        iTerm's line numbers are absolute and the mutable screen can contain
        trailing blank rows.  Reading from ``total - N`` therefore both loses
        freshly printed output and can address a valid-looking but empty range.
        Include the visible screen first, then extend into scrollback only when
        the caller asks for more lines.
        """

        overflow = int(info.overflow)
        visible_start = max(overflow, int(info.first_visible_line_number))
        visible_height = max(0, int(info.mutable_area_height))
        if visible_height == 0:
            return overflow, 0
        history_needed = max(0, int(requested_lines) - visible_height)
        first_line = max(overflow, visible_start - history_needed)
        return first_line, visible_start + visible_height - first_line

    async def read_line_count(self, target: SessionTarget) -> int:
        try:
            session = await self._session(target)
            assert self._connection is not None
            return await asyncio.wait_for(
                self._read_line_count_rpc(session), self._rpc_timeout
            )
        except SessionUnavailableError:
            raise
        except Exception as exc:
            raise TerminalOperationError(f"Failed to read terminal line count: {exc}") from exc

    async def _read_line_count_rpc(self, session: Any) -> int:
        assert self._connection is not None
        async with Transaction(self._connection):
            info = await session.async_get_line_info()
            return int(info.scrollback_buffer_height + info.mutable_area_height)

    async def send_control(self, target: SessionTarget, code: int) -> None:
        try:
            session = await self._session(target)
            lock = self._session_send_locks.setdefault(target.session_id, asyncio.Lock())
            async with lock:
                await asyncio.wait_for(
                    session.async_send_text(chr(code)), self._rpc_timeout
                )
        except SessionUnavailableError:
            raise
        except Exception as exc:
            raise TerminalOperationError(f"Failed to send control character: {exc}") from exc

    async def wait_for_completion(self, target: SessionTarget) -> None:
        """Compatibility hook for ports that split send and wait operations."""

        return None
