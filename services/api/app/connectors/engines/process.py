"""Run a collection engine as an isolated, bounded subprocess.

Arguments are always an argument list (never a shell string). The child gets a minimal
environment without proxy settings or application secrets, its own process group so the whole
tree can be stopped, and a private temporary home. Output is read line by line so progress and
cancellation are handled while it runs; output beyond the line limit stops the process.
"""

from __future__ import annotations

import contextlib
import os
import queue
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

MAX_LINE_BYTES = 64 * 1024


@dataclass
class ProcessResult:
    returncode: int | None
    stopped: str | None  # "timeout", "canceled", "output_limit" or None
    stdout: list[str] = field(default_factory=list)
    stderr: list[str] = field(default_factory=list)


def minimal_environment(home: Path, extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": str(home),
        "TMPDIR": str(home),
        "LANG": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
    }
    if "VIRTUAL_ENV" in os.environ:
        env["PATH"] = f"{Path(os.environ['VIRTUAL_ENV']) / 'bin'}:{env['PATH']}"
    env.update(extra or {})
    return env


def _reader(stream: object, sink: queue.Queue[tuple[str, str | None]], name: str) -> None:
    assert hasattr(stream, "readline")
    try:
        while True:
            raw = stream.readline(MAX_LINE_BYTES)
            if not raw:
                break
            sink.put((name, raw.decode("utf-8", errors="replace").rstrip("\r\n")))
    finally:
        sink.put((name, None))


def run(
    args: Sequence[str],
    *,
    env: dict[str, str],
    cwd: Path | None,
    deadline: float,
    cancelled: Callable[[], bool],
    stdin: bytes | None = None,
    on_stdout: Callable[[str], None] | None = None,
    max_lines: int = 50_000,
    stop_when: Callable[[], bool] | None = None,
) -> ProcessResult:
    process = subprocess.Popen(  # noqa: S603 - fixed argument list, no shell
        list(args),
        stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        cwd=cwd,
        start_new_session=True,
    )
    if stdin is not None and process.stdin is not None:
        with contextlib.suppress(BrokenPipeError):
            process.stdin.write(stdin)
        process.stdin.close()
    lines: queue.Queue[tuple[str, str | None]] = queue.Queue()
    readers = [
        threading.Thread(target=_reader, args=(process.stdout, lines, "stdout"), daemon=True),
        threading.Thread(target=_reader, args=(process.stderr, lines, "stderr"), daemon=True),
    ]
    for reader in readers:
        reader.start()
    result = ProcessResult(returncode=None, stopped=None)
    open_streams = 2
    try:
        while open_streams:
            if result.stopped is None:
                if cancelled():
                    result.stopped = "canceled"
                elif time.monotonic() > deadline:
                    result.stopped = "timeout"
                elif _over_limit(result, max_lines, stop_when):
                    result.stopped = "output_limit"
                if result.stopped is not None:
                    _terminate(process)
            try:
                name, line = lines.get(timeout=0.2)
            except queue.Empty:
                continue
            if line is None:
                open_streams -= 1
                continue
            if name == "stdout":
                result.stdout.append(line)
                if on_stdout is not None:
                    on_stdout(line)
            else:
                result.stderr.append(line)
    finally:
        if process.poll() is None:
            _terminate(process)
        result.returncode = process.wait(timeout=10)
    return result


def _over_limit(
    result: ProcessResult, max_lines: int, stop_when: Callable[[], bool] | None
) -> bool:
    if len(result.stdout) + len(result.stderr) > max_lines:
        return True
    return stop_when is not None and stop_when()


def _terminate(process: subprocess.Popen[bytes]) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(process.pid, signal.SIGKILL)
