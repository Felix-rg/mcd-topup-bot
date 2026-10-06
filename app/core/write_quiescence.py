"""Process-local write admission fence for a temporary staging maintenance window.

This module deliberately has no database, FastAPI, or application-startup imports.
It is safe to use before a route dependency, audit helper, or provider client is
entered.  The fence is only authoritative for the documented one-process,
one-replica staging topology; deployment inventory remains the authority for
replica and separate-worker verification.
"""

from __future__ import annotations

import asyncio
import os
import threading
from contextlib import asynccontextmanager
from typing import AsyncIterator, Dict, Tuple

from app.core.settings import settings


class WriteQuiescenceActive(RuntimeError):
    """Raised before a new protected writer can start during maintenance."""


WriterOwner = Tuple[int, asyncio.Task[object]]


class WriteQuiescenceGate:
    """Atomically stop new writer admission and account for already-admitted work.

    A lease is re-entrant only for the same asyncio task.  This avoids treating
    a child task as admitted merely because context from its parent was copied.
    The counter represents top-level protected writer sections, so nested
    service guards do not inflate the drain result.
    """

    def __init__(self, *, enabled: bool = False) -> None:
        self._lock = threading.RLock()
        self._drained = threading.Event()
        self._drained.set()
        self._enabled = bool(enabled)
        self._engine_stopped = False
        self._active_writers = 0
        self._leases: Dict[WriterOwner, int] = {}

    @property
    def enabled(self) -> bool:
        with self._lock:
            return self._enabled

    @property
    def active_writers(self) -> int:
        with self._lock:
            return self._active_writers

    @staticmethod
    def _single_process_runtime() -> bool:
        """Reject explicit multi-worker configuration.

        An application process cannot discover Railway replica count.  The
        rollout procedure therefore verifies replica/service inventory outside
        the process; this check makes the in-process readiness fail closed when
        a supported worker-count signal is not exactly one.
        """

        configured_counts: list[int] = []
        for variable in ("WEB_CONCURRENCY", "UVICORN_WORKERS", "GUNICORN_WORKERS"):
            raw_value = os.getenv(variable)
            if raw_value is None or not raw_value.strip():
                continue
            try:
                count = int(raw_value.strip())
            except ValueError:
                return False
            if count != 1:
                return False
            configured_counts.append(count)
        return not configured_counts or all(count == 1 for count in configured_counts)

    def _ready_locked(self) -> bool:
        return self._enabled and self._engine_stopped and self._active_writers == 0 and self._single_process_runtime()

    @property
    def ready(self) -> bool:
        with self._lock:
            return self._ready_locked()

    def begin_quiescence(self) -> None:
        """Close admission before a readiness observation can be made."""

        with self._lock:
            self._enabled = True

    def mark_engine_stopped(self) -> None:
        with self._lock:
            if self._enabled:
                self._engine_stopped = True

    def maintenance_payload(self) -> dict[str, object]:
        """Return only safe evidence suitable for the health acknowledgement."""

        with self._lock:
            return {
                "write_gate": "enabled",
                "engine": "stopped" if self._engine_stopped else "not_stopped",
                "engine_stopped": self._engine_stopped,
                "in_flight_writers": self._active_writers,
                "database_writes_allowed": False,
                "ready": self._ready_locked(),
                "single_process_runtime": self._single_process_runtime(),
            }

    @staticmethod
    def _owner() -> WriterOwner:
        task = asyncio.current_task()
        if task is None:  # pragma: no cover - asynccontextmanager always has a task
            raise RuntimeError("Writer admission requires an asyncio task")
        return (threading.get_ident(), task)

    @asynccontextmanager
    async def writer_section(self, _label: str) -> AsyncIterator[None]:
        """Admit one protected writer or reject it before it can mutate state."""

        owner = self._owner()
        top_level = False
        with self._lock:
            depth = self._leases.get(owner, 0)
            if depth:
                self._leases[owner] = depth + 1
            else:
                if self._enabled:
                    raise WriteQuiescenceActive("Staging writes are quiesced")
                self._leases[owner] = 1
                self._active_writers += 1
                self._drained.clear()
                top_level = True

        try:
            yield
        finally:
            with self._lock:
                depth = self._leases.get(owner, 0)
                if depth <= 1:
                    self._leases.pop(owner, None)
                    if top_level:
                        self._active_writers -= 1
                        if self._active_writers == 0:
                            self._drained.set()
                else:
                    self._leases[owner] = depth - 1

    async def wait_for_drain(self, timeout: float | None = None) -> bool:
        """Wait without blocking the event loop; used by tests and operators' tooling."""

        return await asyncio.to_thread(self._drained.wait, timeout)


write_quiescence = WriteQuiescenceGate(enabled=settings.write_quiescence)
