"""Supervise fresh, memory-limited calendar workers and collect bounded output."""

import json
from pathlib import Path
import subprocess
import sys

import anyio

WORKER_PATH = Path(__file__).with_name("calendar_worker.py")


class CalendarProcessingError(Exception):
    """The calendar could not be processed within resource limits."""


class CalendarProcessingTimeout(CalendarProcessingError):
    """The worker exceeded its wall-clock budget."""


class CalendarProcessingCancelled(CalendarProcessingError):
    """The requesting client is no longer waiting."""


class CalendarProcessStartError(CalendarProcessingError):
    """The worker could not be started."""


def require_linux():
    if sys.platform != "linux":
        raise RuntimeError("Calendar processing requires Linux with RLIMIT_AS support")
    import resource
    if not hasattr(resource, "RLIMIT_AS"):
        raise RuntimeError("Calendar processing requires Linux with RLIMIT_AS support")


async def process_calendar(payload, *, timeout, memory_mib, max_response_bytes, cancelled):
    process = None
    output = bytearray()
    code = None

    async def write_input():
        data = await anyio.to_thread.run_sync(lambda: json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        for offset in range(0, len(data), 65536):
            await process.stdin.send(data[offset:offset + 65536])
        await process.stdin.aclose()

    async def read_output():
        while True:
            try:
                chunk = await process.stdout.receive(min(65536, max_response_bytes - len(output) + 1))
            except anyio.EndOfStream:
                break
            output.extend(chunk)
            if len(output) > max_response_bytes:
                raise CalendarProcessingError("Calendar response is too large")

    async def watch_cancel(scope):
        await cancelled.wait()
        scope.cancel()

    try:
        with anyio.move_on_after(timeout) as scope:
            async with anyio.create_task_group() as watchers:
                watchers.start_soon(watch_cancel, scope)
                # Obtain a handle even if cancellation arrives during process creation.
                with anyio.CancelScope(shield=True):
                    process = await anyio.open_process(
                        [sys.executable, "-u", str(WORKER_PATH), str(memory_mib * 1024 * 1024)],
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    )
                async with anyio.create_task_group() as io:
                    io.start_soon(write_input)
                    io.start_soon(read_output)
                    code = await process.wait()
                watchers.cancel_scope.cancel()
    except Exception:
        if process is None:
            raise CalendarProcessStartError("Unable to start calendar processor") from None
        raise CalendarProcessingError("Unable to process calendar") from None
    finally:
        with anyio.CancelScope(shield=True):
            if process is not None:
                if process.returncode is None:
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
                await process.wait()
                await process.aclose()
    if cancelled.is_set():
        raise CalendarProcessingCancelled("Calendar request cancelled")
    if scope.cancel_called:
        raise CalendarProcessingTimeout("Calendar processing timed out")
    if code != 0 or not output:
        raise CalendarProcessingError("Unable to process calendar")
    return bytes(output)
