"""Real subprocess limits, cleanup, and compatibility at the application boundary."""

import asyncio
from dataclasses import replace
from datetime import datetime
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import anyio
from fastapi.encoders import jsonable_encoder
from fastapi.testclient import TestClient
import httpx2
from icalevents.icalevents import events
import pytest

import calendar_processing as processing
import main

EMPTY = "BEGIN:VCALENDAR\nVERSION:2.0\nEND:VCALENDAR\n"
CALENDAR = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Calvie//Limits//EN
BEGIN:VEVENT
UID:private-event
DTSTART:20260105T100000Z
DTEND:20260105T110000Z
SUMMARY:Meeting <team>
DESCRIPTION:Private description
CLASS:PRIVATE
LOCATION:Room 1
ORGANIZER:mailto:organizer@example.com
ATTENDEE;CN=Person:mailto:person@example.com
CATEGORIES:Work,Review
RRULE:FREQ=DAILY;COUNT=2
END:VEVENT
END:VCALENDAR
"""


@pytest.fixture
def payload():
    return {"content": CALENDAR, "start": "2026-01-01T00:00:00+00:00", "end": "2026-01-10T00:00:00+00:00",
            "timezone": "UTC", "max_events": 5000, "max_response_bytes": 20971520, "format": "json",
            "locale": "en_GB", "width": 300, "colour": None, "color_scheme": None}


@pytest.fixture
def children(monkeypatch):
    processes = []
    real_open = anyio.open_process

    async def opening(*args, **kwargs):
        child = await real_open(*args, **kwargs)
        processes.append(child)
        return child

    monkeypatch.setattr(processing.anyio, "open_process", opening)
    yield processes
    for child in processes:
        assert child.returncode is not None, "child is still running"
        with pytest.raises(ChildProcessError):
            os.waitpid(child.pid, os.WNOHANG)


@pytest.fixture
def probe(tmp_path, monkeypatch):
    script = tmp_path / "resource_probe.py"
    script.write_text('''import json, os, resource, sys, time
limit = int(sys.argv[1])
resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
payload = json.load(sys.stdin)
mode = payload.get("mode", "hang")
if mode == "memory":
    assert resource.getrlimit(resource.RLIMIT_AS) == (limit, limit)
    try:
        data = bytearray(limit * 2)
    except MemoryError:
        sys.exit(2)
    print("memory limit was not enforced")
elif mode == "large":
    while True:
        os.write(1, b"x" * 65536)
elif mode == "failure":
    print("SECRET partial response", flush=True)
    sys.exit(2)
else:
    print("partial response", flush=True)
    while True:
        time.sleep(0.01)
''')
    monkeypatch.setattr(processing, "WORKER_PATH", script)
    return script


async def run(payload, **options):
    defaults = {"timeout": 10, "memory_mib": 256, "max_response_bytes": 20971520,
                "cancelled": anyio.Event()}
    defaults.update(options)
    return await processing.process_calendar(payload, **defaults)


@pytest.mark.asyncio
async def test_complete_json_matches_original_encoder(payload, children):
    result = json.loads(await run(payload))
    expected = events(string_content=CALENDAR, start=datetime.fromisoformat(payload["start"]),
                      end=datetime.fromisoformat(payload["end"]))
    assert result == jsonable_encoder(expected)
    assert len(result) == 2
    assert result[0]["private"] is True
    assert result[0]["attendee"]
    assert result[0]["component"]
    assert "description" in result[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("format", ["json", "html"])
async def test_fresh_processes_and_response_limit(payload, children, format):
    payload["format"] = format
    body = await run(payload)
    assert body
    # Child-side limit: the parent would accept a much larger response.
    payload["max_response_bytes"] = 1
    with pytest.raises(processing.CalendarProcessingError):
        await run(payload)
    payload["max_response_bytes"] = 20971520
    # Parent-side limit is independently enforced.
    with pytest.raises(processing.CalendarProcessingError):
        await run(payload, max_response_bytes=1)
    assert len({child.pid for child in children}) == 3


@pytest.mark.asyncio
async def test_exact_output_and_event_count_boundaries(payload):
    payload["max_events"] = 2
    assert len(json.loads(await run(payload))) == 2
    payload["max_events"] = 1
    with pytest.raises(processing.CalendarProcessingError):
        await run(payload)
    payload["content"] = EMPTY
    payload["max_response_bytes"] = 2
    assert await run(payload, max_response_bytes=2) == b"[]"


@pytest.mark.asyncio
async def test_timeout_kills_and_reaps_process_then_recovers(payload, probe, children, monkeypatch):
    with pytest.raises(processing.CalendarProcessingTimeout):
        await run(payload, timeout=0.25)
    monkeypatch.setattr(processing, "WORKER_PATH", Path(processing.__file__).with_name("calendar_worker.py"))
    assert json.loads(await run(payload))


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["memory", "large", "failure"])
async def test_resource_failure_is_reaped(payload, probe, children, mode):
    payload["mode"] = mode
    with pytest.raises(processing.CalendarProcessingError):
        await run(payload, memory_mib=64, max_response_bytes=1024)


@pytest.mark.asyncio
async def test_real_worker_applies_memory_limit(payload, children):
    with pytest.raises(processing.CalendarProcessingError):
        await run(payload, memory_mib=1)
    assert json.loads(await run(payload))


@pytest.mark.asyncio
async def test_pathological_recurrence_is_bounded(payload, children):
    payload["content"] = CALENDAR.replace("FREQ=DAILY;COUNT=2", "FREQ=SECONDLY")
    payload["end"] = "2027-01-01T00:00:00+00:00"
    with pytest.raises((processing.CalendarProcessingTimeout, processing.CalendarProcessingError)):
        await run(payload, timeout=1, memory_mib=96)
    payload["content"] = EMPTY
    assert await run(payload) == b"[]"


@pytest.mark.asyncio
async def test_explicit_cancellation_cleans_up(payload, probe, children):
    cancelled = anyio.Event()
    job = asyncio.create_task(run(payload, cancelled=cancelled))
    with anyio.fail_after(2):
        while not children:
            await anyio.sleep(0.01)
    cancelled.set()
    with pytest.raises(processing.CalendarProcessingCancelled):
        await job


@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_unsupported_platform_fails_at_startup(monkeypatch, platform):
    import runpy
    monkeypatch.setattr(processing.sys, "platform", platform)
    with pytest.raises(RuntimeError, match="requires Linux"):
        runpy.run_module("main", run_name="__unsupported_platform__")


@pytest.mark.parametrize("route", ["cal", "iframe"])
def test_process_start_failure_is_generic_500(monkeypatch, route):
    async def fail(*_args, **_kwargs):
        raise OSError("SECRET")
    monkeypatch.setattr(processing.anyio, "open_process", fail)
    monkeypatch.setattr(main, "download_calendar", AsyncMock(return_value=EMPTY))
    main.CONFIG["test"] = {"url": "https://example.com/SECRET.ics"}
    result = TestClient(main.app).get(f"/{route}/test")
    assert result.status_code == 500
    assert "SECRET" not in result.text
    assert main.calendar_slots.borrowed_tokens == 0


@pytest.mark.parametrize("route", ["cal", "iframe"])
def test_partial_worker_failure_never_becomes_success(monkeypatch, route, probe, children):
    # The probe prints secret content, then fails, regardless of the renderer.
    async def failed_processor(payload, **kwargs):
        return await processing.process_calendar({**payload, "mode": "failure"}, **kwargs)
    monkeypatch.setattr(main, "process_calendar", failed_processor)
    monkeypatch.setattr(main, "download_calendar", AsyncMock(return_value=EMPTY))
    main.CONFIG["test"] = {"url": "https://example.com/SECRET.ics"}
    result = TestClient(main.app).get(f"/{route}/test")
    assert result.status_code == 400
    assert "SECRET" not in result.text
    assert "partial response" not in result.text


@pytest.mark.asyncio
@pytest.mark.parametrize("cancellation", ["task", "disconnect"])
async def test_request_cancellation_stops_child_and_restores_capacity(monkeypatch, probe, children, cancellation):
    from fastapi import HTTPException
    from starlette.requests import Request

    main.CONFIG["test"] = {"url": "https://example.com/calendar.ics"}
    monkeypatch.setattr(main, "download_calendar", AsyncMock(return_value=EMPTY))
    monkeypatch.setattr(main, "calendar_slots", anyio.CapacityLimiter(1))
    disconnected = anyio.Event()

    async def receive():
        # An available ASGI message must return without a cancellation checkpoint.
        if not disconnected.is_set():
            await disconnected.wait()
        return {"type": "http.disconnect"}

    request = Request({"type": "http", "headers": []}, receive)
    task = asyncio.create_task(main.cal_data("test", request))
    with anyio.fail_after(2):
        while not children:
            await anyio.sleep(0.01)
    assert main.calendar_slots.borrowed_tokens == 1
    with anyio.fail_after(2):
        if cancellation == "task":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            disconnected.set()
            with pytest.raises(HTTPException) as error:
                await task
            assert error.value.status_code == 400
        await asyncio.gather(*tuple(main.calendar_jobs), return_exceptions=True)
    assert main.calendar_slots.borrowed_tokens == 0
    assert children[0].returncode is not None
    monkeypatch.setattr(processing, "WORKER_PATH", Path(processing.__file__).with_name("calendar_worker.py"))
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=main.app), base_url="http://test") as client:
        assert (await client.get("/")).status_code == 418
        assert (await client.get("/cal/test")).status_code == 200


@pytest.mark.parametrize("route", ["cal", "iframe"])
def test_processing_timeout_is_504_and_releases_slot(monkeypatch, route, probe, children):
    monkeypatch.setattr(main, "download_calendar", AsyncMock(return_value=EMPTY))
    monkeypatch.setattr(main, "LIMITS", replace(main.LIMITS, parser_timeout=1))
    main.CONFIG["test"] = {"url": "https://example.com/SECRET.ics"}
    result = TestClient(main.app).get(f"/{route}/test")
    assert result.status_code == 504
    assert "SECRET" not in result.text
    assert main.calendar_slots.borrowed_tokens == 0


@pytest.mark.asyncio
async def test_four_real_children_keep_root_responsive(monkeypatch, probe, children):
    main.CONFIG["test"] = {"url": "https://example.com/calendar.ics"}
    monkeypatch.setattr(main, "download_calendar", AsyncMock(return_value=EMPTY))
    monkeypatch.setattr(main, "calendar_slots", anyio.CapacityLimiter(4))
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=main.app), base_url="http://test") as client:
        jobs = [asyncio.create_task(client.get(f"/{route}/test")) for route in ["cal", "iframe", "cal", "iframe"]]
        try:
            with anyio.fail_after(3):
                while len(children) < 4:
                    await anyio.sleep(0.01)
            assert (await asyncio.wait_for(client.get("/"), 0.5)).status_code == 418
            assert (await client.get("/cal/test")).status_code == 503
            assert len(children) == 4
        finally:
            for task in jobs:
                task.cancel()
            await asyncio.gather(*jobs, return_exceptions=True)
            await asyncio.gather(*tuple(main.calendar_jobs), return_exceptions=True)
        assert main.calendar_slots.borrowed_tokens == 0


@pytest.mark.asyncio
async def test_pathological_calendar_does_not_block_application(monkeypatch, children):
    main.CONFIG["test"] = {"url": "https://example.com/calendar.ics"}
    monkeypatch.setattr(main, "download_calendar", AsyncMock(return_value=CALENDAR.replace("FREQ=DAILY;COUNT=2", "FREQ=SECONDLY")))
    monkeypatch.setattr(main, "LIMITS", replace(main.LIMITS, parser_timeout=1, parser_memory_mib=96))
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=main.app), base_url="http://test") as client:
        job = asyncio.create_task(client.get("/cal/test"))
        with anyio.fail_after(2):
            while not children:
                await anyio.sleep(0.01)
        assert (await asyncio.wait_for(client.get("/"), 0.5)).status_code == 418
        # Allow CI scheduling and kill/reap overhead; the worker budget above stays one second.
        assert (await asyncio.wait_for(job, 10)).status_code in {400, 504}
        assert main.calendar_slots.borrowed_tokens == 0
        monkeypatch.setattr(main, "download_calendar", AsyncMock(return_value=EMPTY))
        assert (await client.get("/cal/test")).status_code == 200


def test_global_processing_limits_are_not_overridable(monkeypatch):
    main.CONFIG["test"] = {"url": "https://example.com/calendar.ics", "parser timeout": "999",
                           "parser memory mib": "999", "max response bytes": "99999999"}
    processor = AsyncMock(return_value=b"[]")
    monkeypatch.setattr(main, "download_calendar", AsyncMock(return_value=EMPTY))
    monkeypatch.setattr(main, "process_calendar", processor)
    assert TestClient(main.app).get("/cal/test?parser_timeout=999&max_response_bytes=99999999").status_code == 200
    kwargs = processor.call_args.kwargs
    assert (kwargs["timeout"], kwargs["memory_mib"], kwargs["max_response_bytes"]) == (10, 256, 20971520)
    assert processor.call_args.args[0]["max_response_bytes"] == 20971520
