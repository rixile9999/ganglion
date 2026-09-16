"""Single-writer serialisation for the operator console.

Implements the ``writer.py`` clause of [[console_operator]]
(docs/tasks/console_operator.md §Scope): **one** daemon thread drains a
``queue.Queue`` and every file write the console causes — ``TraceStore.append``,
``LabelStore.append``, ``DecisionStore.append``, ``write_manifest`` /
``write_run_bundle``, ``ledger.emit``, ``register_compiled``, ``analyze_run``,
``compare_runs``, ``write_exports`` — plus the GPU-bound
``load_local_model`` / ``unload_local_model`` goes through it.

Why a queue and not a lock: the stores each carry their own ``RLock``, so a
lock would serialise *within* a store but still interleave two multi-file
side effects (a chat writes a trace **and** two ledger rows). One writer
thread makes every console side effect a single ordered job, which is what
the "no interleaved lines" invariant in the composite's ``success`` clause
asks for.

``submit`` is synchronous: the calling HTTP thread blocks until the job ran
and gets the return value, or the job's exception re-raised (the handler
turns that into a 500). Nothing is fire-and-forget — a response is never
sent before its bytes are on disk.

Public API:
    Writer, WriterDeadError.
"""

from __future__ import annotations

import queue
import threading
import time
from collections import deque
from typing import Any, Callable

__all__ = ["Writer", "WriterDeadError"]

_STOP = object()


class WriterDeadError(RuntimeError):
    """The writer thread is not running — every W route answers 503."""


class _Job:
    """One queued call plus the caller's completion latch."""

    __slots__ = ("fn", "args", "kwargs", "done", "result", "error", "queued_at")

    def __init__(self, fn: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.done = threading.Event()
        self.result: Any = None
        self.error: BaseException | None = None
        self.queued_at = time.perf_counter()


class Writer:
    """One daemon thread + queue; ``submit`` blocks and re-raises.

    Observability (composite ``Observation``): ``queue_depth_max`` and
    ``latency_ms_p95()`` over the last 512 jobs — the serialisation cost of
    the single writer.
    """

    def __init__(self, *, name: str = "console-writer", history: int = 512) -> None:
        self._queue: queue.Queue[Any] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._name = name
        self._lock = threading.Lock()
        self._latencies: deque[float] = deque(maxlen=history)
        self.queue_depth_max: int = 0
        self.job_count: int = 0

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> "Writer":
        """Start the daemon thread (idempotent); returns ``self`` for chaining."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return self
            thread = threading.Thread(target=self._run, name=self._name, daemon=True)
            self._thread = thread
            thread.start()
        return self

    @property
    def alive(self) -> bool:
        """True while the thread is running — W routes 503 when it is not."""
        thread = self._thread
        return thread is not None and thread.is_alive()

    def stop(self, timeout: float | None = 5.0) -> None:
        """Drain the queue, stop the thread and join it (idempotent)."""
        with self._lock:
            thread = self._thread
            self._thread = None
        if thread is None:
            return
        self._queue.put(_STOP)
        thread.join(timeout)

    # -- work --------------------------------------------------------------

    def submit(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Run ``fn(*args, **kwargs)`` on the writer thread; block for the result.

        The job's exception is re-raised in the caller's thread (with its
        original traceback), so a handler can map ``ValueError`` to 409 and
        anything else to 500 exactly as if it had called the primitive
        directly.

        Calling ``submit`` *from* the writer thread (a primitive that itself
        submits) would deadlock, so that case runs inline — the ordering
        guarantee already holds there.
        """
        if threading.current_thread() is self._thread:
            return fn(*args, **kwargs)
        if not self.alive:
            raise WriterDeadError("console writer thread is not running")
        job = _Job(fn, args, kwargs)
        self._queue.put(job)
        depth = self._queue.qsize()
        if depth > self.queue_depth_max:
            self.queue_depth_max = depth
        job.done.wait()
        if job.error is not None:
            raise job.error
        return job.result

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is _STOP:
                self._queue.task_done()
                return
            job: _Job = item
            started = time.perf_counter()
            try:
                job.result = job.fn(*job.args, **job.kwargs)
            except BaseException as exc:  # noqa: BLE001 — re-raised in the caller
                job.error = exc
            finally:
                self._latencies.append((time.perf_counter() - started) * 1000.0)
                self.job_count += 1
                job.done.set()
                self._queue.task_done()

    # -- observability -----------------------------------------------------

    def latency_ms_p95(self) -> float:
        """p95 of the recorded job runtimes (``0.0`` before the first job)."""
        samples = sorted(self._latencies)
        if not samples:
            return 0.0
        index = min(len(samples) - 1, int(round(0.95 * (len(samples) - 1))))
        return round(samples[index], 3)

    def stats(self) -> dict[str, Any]:
        """``{"alive", "jobs", "queue_depth_max", "latency_ms_p95"}``."""
        return {
            "alive": self.alive,
            "jobs": self.job_count,
            "queue_depth_max": self.queue_depth_max,
            "latency_ms_p95": self.latency_ms_p95(),
        }

    # -- context manager ---------------------------------------------------

    def __enter__(self) -> "Writer":
        return self.start()

    def __exit__(self, *exc_info: Any) -> None:
        self.stop()
