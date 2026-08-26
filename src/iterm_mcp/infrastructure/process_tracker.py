"""Fallback process/TTY completion detection for sessions without shell integration."""

from __future__ import annotations

import asyncio
import os
import shlex
import time
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProcessSnapshot:
    pid: str
    pgid: str
    cpu_percent: float


class ProcessTracker:
    """Uses macOS ps output without invoking a shell pipeline."""

    async def _run(self, *args: str) -> str:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await process.communicate()
        if process.returncode != 0:
            return ""
        return stdout.decode(errors="replace")

    async def active_process(self, tty_path: str) -> ProcessSnapshot | None:
        if not tty_path or not os.path.exists(tty_path):
            return None
        tty_name = tty_path.rsplit("/", 1)[-1]
        foreground_output = await self._run("ps", "-o", "pgid=", "-t", tty_name)
        foreground_pgid = next(
            (line.split()[0] for line in foreground_output.splitlines() if line.split()),
            None,
        )
        if foreground_pgid is None:
            return None
        output = await self._run(
            "ps", "-t", tty_name, "-o", "pid=,pgid=,%cpu=", "-w"
        )
        snapshots: list[ProcessSnapshot] = []
        for line in output.splitlines():
            parts = line.split()
            if len(parts) >= 3:
                try:
                    snapshots.append(
                        ProcessSnapshot(parts[0], parts[1], float(parts[2]))
                    )
                except ValueError:
                    continue
        foreground_processes = [
            snapshot for snapshot in snapshots if snapshot.pgid == foreground_pgid
        ]
        return max(foreground_processes, key=lambda snapshot: snapshot.cpu_percent, default=None)

    async def wait_until_idle(self, tty_path: str, timeout: float = 120.0) -> None:
        """Wait until the foreground TTY process is absent or CPU-idle."""

        deadline = time.monotonic() + timeout
        idle_since: float | None = None
        while time.monotonic() < deadline:
            snapshot = await self.active_process(tty_path)
            if snapshot is None:
                return
            if snapshot.cpu_percent < 1.0:
                idle_since = idle_since or time.monotonic()
                if time.monotonic() - idle_since >= 1.0:
                    return
            else:
                idle_since = None
            await asyncio.sleep(0.35)
        raise TimeoutError(f"Timed out waiting for terminal process on {shlex.quote(tty_path)}")
