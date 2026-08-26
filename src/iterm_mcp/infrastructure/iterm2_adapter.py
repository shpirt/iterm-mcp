"""Adapter for iTerm2's long-lived Python API connection."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from typing import Any, cast

from iterm2.app import async_get_app
from iterm2.capabilities import AppVersionTooOld
from iterm2.connection import Connection
from iterm2.prompt import PromptMonitor
from iterm2.transaction import Transaction

from iterm_mcp.domain.models import SessionTarget, SessionUnavailableError, TerminalOperationError

from .output_reader import normalize_lines
from .process_tracker import ProcessTracker

logger = logging.getLogger(__name__)


class Iterm2Adapter:
    """Implements terminal operations against one iTerm2 App connection."""

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
            session = self._require_app().get_session_by_id(session_id, include_buried=True)
            return await self._target_for_session(session) if session else None
        except TerminalOperationError:
            raise
        except Exception as exc:
            raise TerminalOperationError(
                f"Failed to resolve iTerm2 session {session_id}: {exc}"
            ) from exc

    async def _session(self, target: SessionTarget) -> Any:
        await self.connect()
        session = self._require_app().get_session_by_id(target.session_id, include_buried=True)
        if session is None:
            raise SessionUnavailableError(f"iTerm2 session not found: {target.session_id}")
        return session

    async def _send_text_to_session(self, session: Any, text: str) -> None:
        try:
            await asyncio.wait_for(
                session.async_send_text(text + "\n"), self._rpc_timeout
            )
        except Exception as exc:
            raise TerminalOperationError(f"Failed to send text: {exc}") from exc

    async def send_text(self, target: SessionTarget, text: str) -> None:
        await self._send_text_to_session(await self._session(target), text)

    async def execute_command(self, target: SessionTarget, text: str) -> None:
        """Subscribe before sending so command-end cannot be missed."""

        session = await self._session(target)
        sent = False
        try:
            assert self._connection is not None
            async with PromptMonitor(
                self._connection,
                target.session_id,
                modes=[PromptMonitor.Mode.COMMAND_END],
            ) as monitor:
                await self._send_text_to_session(session, text)
                sent = True
                await asyncio.wait_for(monitor.async_get(), self._completion_timeout)
                return
        except (TimeoutError, AppVersionTooOld):
            logger.debug("Prompt monitor unavailable; using TTY fallback", exc_info=True)
        except SessionUnavailableError:
            raise
        except TerminalOperationError:
            raise
        except Exception:
            logger.debug("Prompt monitor failed; using TTY fallback", exc_info=True)

        if not sent:
            await self._send_text_to_session(session, text)

        if target.tty_path is None:
            raise TerminalOperationError("No TTY is available for completion detection")
        try:
            await self._process_tracker.wait_until_idle(target.tty_path, self._completion_timeout)
        except TimeoutError as exc:
            raise TerminalOperationError(str(exc)) from exc

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
            await asyncio.wait_for(
                session.async_send_text(chr(code)), self._rpc_timeout
            )
        except SessionUnavailableError:
            raise
        except Exception as exc:
            raise TerminalOperationError(f"Failed to send control character: {exc}") from exc

    async def wait_for_completion(self, target: SessionTarget) -> None:
        """Compatibility hook for ports that split send and wait operations."""

        if target.tty_path:
            await self._process_tracker.wait_until_idle(target.tty_path, self._completion_timeout)
