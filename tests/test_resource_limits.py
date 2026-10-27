"""Bounded calendar work, including cancellation and actual compressed streams."""

import asyncio
import configparser
import gzip
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import runpy
import threading
from unittest.mock import Mock
import zlib

import anyio
from fastapi import HTTPException
from fastapi.testclient import TestClient
import httpx2
import pytest
import urllib3

import calendar_source as source
import main


@pytest.fixture
def calendar(monkeypatch):
    main.CONFIG["test"] = {"url": "https://example.com/SECRET.ics"}
    download = Mock(return_value="calendar")
    parser = Mock(return_value=[])
    monkeypatch.setattr(main, "download_calendar", download)
    monkeypatch.setattr(main, "events", parser)
    monkeypatch.setattr(main, "calendar_slots", anyio.CapacityLimiter(4))
    monkeypatch.setattr(main, "calendar_threads", anyio.CapacityLimiter(4))
    return download, parser


@pytest.mark.parametrize("stage", ["download", "parser"])
@pytest.mark.asyncio
async def test_four_jobs_do_not_block_root_and_fifth_fails_immediately(calendar, stage):
    download, parser = calendar
    release = threading.Event()
    started = asyncio.Event()
    loop = asyncio.get_running_loop()
    event_loop_thread = threading.get_ident()
    count = 0
    lock = threading.Lock()

    def blocking(*_args, **_kwargs):
        nonlocal count
        assert threading.get_ident() != event_loop_thread
        with lock:
            count += 1
            if count == 4:
                loop.call_soon_threadsafe(started.set)
        assert release.wait(5), "test failed to release worker"
        return "calendar" if stage == "download" else []

    (download if stage == "download" else parser).side_effect = blocking
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=main.app), base_url="http://test") as client:
        jobs = [asyncio.create_task(client.get(f"/{route}/test")) for route in ["cal", "iframe", "cal", "iframe"]]
        try:
            await asyncio.wait_for(started.wait(), 2)
            assert (await asyncio.wait_for(client.get("/"), 0.5)).status_code == 418
            for route in ["cal", "iframe"]:
                busy = await asyncio.wait_for(client.get(f"/{route}/test"), 0.5)
                assert busy.status_code == 503
                assert busy.headers["Retry-After"] == "1"
                assert "SECRET" not in busy.text
            assert count == 4
        finally:
            release.set()
            results = await asyncio.gather(*jobs)
        assert all(result.status_code == 200 for result in results)
        assert main.calendar_slots.borrowed_tokens == 0
        assert (await client.get("/cal/test")).status_code == 200


@pytest.mark.parametrize("cancellation", ["asyncio", "anyio"])
@pytest.mark.parametrize("failure", [False, True])
@pytest.mark.asyncio
async def test_cancelled_request_keeps_slot_until_worker_finishes(calendar, monkeypatch, cancellation, failure):
    download, parser = calendar
    monkeypatch.setattr(main, "calendar_slots", anyio.CapacityLimiter(1))
    release = threading.Event()
    started = asyncio.Event()
    loop = asyncio.get_running_loop()

    def blocking(*_args, **_kwargs):
        loop.call_soon_threadsafe(started.set)
        assert release.wait(5)
        if failure:
            raise ValueError("SECRET")
        return "calendar"

    download.side_effect = blocking
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=main.app), base_url="http://test") as client:
        scope = anyio.CancelScope()

        async def requesting():
            with scope:
                return await client.get("/cal/test")

        request = asyncio.create_task(requesting())
        try:
            await asyncio.wait_for(started.wait(), 2)
            if cancellation == "asyncio":
                request.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await request
            else:
                scope.cancel()
                await asyncio.wait_for(request, 0.5)
            assert main.calendar_slots.borrowed_tokens == 1
            assert (await client.get("/iframe/test")).status_code == 503
        finally:
            release.set()
            await asyncio.gather(request, return_exceptions=True)
            await asyncio.gather(*tuple(main.calendar_jobs), return_exceptions=True)
        assert main.calendar_slots.borrowed_tokens == 0
        download.side_effect = None
        assert (await client.get("/cal/test")).status_code == 200


@pytest.mark.parametrize("route", ["cal", "iframe"])
@pytest.mark.parametrize("days", [0, -1, 367, 999999999999])
def test_invalid_days_rejected_before_download(calendar, route, days):
    result = TestClient(main.app).get(f"/{route}/test", params={"days": days})
    assert result.status_code == 422
    calendar[0].assert_not_called()


@pytest.mark.parametrize("days", ["0", "-1", "367", "invalid"])
def test_configured_days_are_also_limited(calendar, days):
    main.CONFIG["test"]["days to future"] = days
    assert TestClient(main.app).get("/cal/test").status_code == 422
    calendar[0].assert_not_called()


@pytest.mark.parametrize("days", [1, 366, None])
def test_valid_days_and_default(calendar, days):
    params = {} if days is None else {"days": days}
    assert TestClient(main.app).get("/cal/test", params=params).status_code == 200
    assert calendar[1].call_args.kwargs["end"].tzinfo is not None


@pytest.mark.asyncio
async def test_event_limit_accepts_boundary_and_checks_before_conversion(calendar):
    event = Mock(all_day=False)
    calendar[1].return_value = [event] * 5000
    assert len(await main.cal_data("test")) == 5000
    assert event.astimezone.call_count == 5000
    event.reset_mock()
    calendar[1].return_value = [event] * 5001
    with pytest.raises(HTTPException) as caught:
        await main.cal_data("test")
    assert caught.value.status_code == 400
    event.astimezone.assert_not_called()
    assert main.calendar_slots.borrowed_tokens == 0


@pytest.mark.parametrize("route", ["cal", "iframe"])
def test_global_limits_cannot_be_increased_by_calendar_or_query(calendar, route):
    main.CONFIG["test"]["max days"] = "1000"
    main.CONFIG["test"]["max events"] = "10000"
    main.CONFIG["test"]["max calendar bytes"] = "99999999"
    client = TestClient(main.app)
    assert client.get(f"/{route}/test?days=367&max_days=1000").status_code == 422
    assert client.get(f"/{route}/test?max_calendar_bytes=99999999").status_code == 200
    calendar[0].assert_called_once_with("https://example.com/SECRET.ics", allow_private_network=False,
                                      max_bytes=5242880, timeout=60)
    calendar[1].return_value = [Mock()] * 5001
    assert client.get(f"/{route}/test").status_code == 400


@pytest.mark.parametrize("route", ["cal", "iframe"])
@pytest.mark.parametrize("error", [source.CalendarDownloadTimeout("SECRET"),
                                 urllib3.exceptions.ReadTimeoutError(None, "/SECRET", "timeout")])
def test_timeout_status_and_redaction_release_slot(calendar, route, error):
    calendar[0].side_effect = error
    result = TestClient(main.app).get(f"/{route}/test")
    assert result.status_code == 504
    assert "SECRET" not in result.text
    assert main.calendar_slots.borrowed_tokens == 0


@pytest.mark.parametrize("option", ["max calendar bytes", "max days", "max events", "download timeout", "max concurrent calendars"])
@pytest.mark.parametrize("value", ["0", "-1", "1.5", "invalid", ""])
def test_invalid_global_limits_fail_at_startup(option, value, monkeypatch):
    def configured_read(config, *_args, **_kwargs):
        config["DEFAULT"][option] = value
    monkeypatch.setattr(configparser.ConfigParser, "read", configured_read)
    with pytest.raises(ValueError, match="positive integer in DEFAULT"):
        runpy.run_module("main", run_name="__test_limits__")


def test_administrator_can_change_global_limits():
    for key, value in {"max calendar bytes": "1024", "max days": "10", "max events": "20",
                       "download timeout": "2", "max concurrent calendars": "1"}.items():
        main.CONFIG["DEFAULT"][key] = value
    limits = main.ResourceLimits.from_config()
    assert limits == main.ResourceLimits(1024, 10, 20, 2, 1)


def body_response(body, headers=None):
    stream = io.BytesIO(body)
    result = Mock(headers=headers or {}, status=200)
    result.read1.side_effect = lambda amount, **_kwargs: stream.read(amount)
    return result


@pytest.mark.parametrize("size,accepted", [(0, False), (127, True), (128, True), (129, False)])
def test_body_size_boundaries(size, accepted):
    response = body_response(b"x" * size)
    if accepted:
        assert source._read_body(response, 128, float("inf")) == b"x" * size
    else:
        with pytest.raises(source.CalendarSourceError):
            source._read_body(response, 128, float("inf"))
    assert all(call.args[0] <= 65536 for call in response.read1.call_args_list)


@pytest.mark.parametrize("length", ["129", "-1", "invalid", "1, 2"])
def test_reject_content_length_before_read(length):
    response = body_response(b"x", {"Content-Length": length})
    with pytest.raises(source.CalendarSourceError):
        source._read_body(response, 128, float("inf"))
    response.read1.assert_not_called()


@pytest.mark.parametrize("headers", [{}, {"Content-Length": "1"}, {"Transfer-Encoding": "chunked"}])
def test_count_actual_bytes_regardless_of_length_header(headers):
    response = body_response(b"x" * 129, headers)
    with pytest.raises(source.CalendarSourceError):
        source._read_body(response, 128, float("inf"))


def raw_deflate(data):
    compressor = zlib.compressobj(wbits=-zlib.MAX_WBITS)
    return compressor.compress(data) + compressor.flush()


@pytest.mark.parametrize("encoding,compress", [("gzip", gzip.compress), ("deflate", zlib.compress),
                                             ("deflate", raw_deflate)])
@pytest.mark.parametrize("size", [128, 129, 1000000])
def test_bounded_decompression(encoding, compress, size):
    data = b"x" * size
    response = body_response(compress(data), {"Content-Encoding": encoding})
    if size <= 128:
        assert source._read_body(response, 128, float("inf")) == data
    else:
        with pytest.raises(source.CalendarSourceError):
            source._read_body(response, 128, float("inf"))


def test_gzip_members_and_split_deflate_header():
    data = gzip.compress(b"first") + gzip.compress(b"second")
    assert source._read_body(body_response(data, {"Content-Encoding": "gzip"}), 128, float("inf")) == b"firstsecond"
    data = zlib.compress(b"calendar")
    response = body_response(b"", {"Content-Encoding": "deflate"})
    response.read1.side_effect = [data[:1], data[1:2], data[2:], b""]
    assert source._read_body(response, 128, float("inf")) == b"calendar"


@pytest.mark.parametrize("encoding,body", [
    ("br", b"data"), ("gzip, deflate", b"data"), ("gzip", b"invalid"),
    ("gzip", gzip.compress(b"calendar")[:-1]), ("deflate", zlib.compress(b"calendar")[:-1]),
    ("deflate", zlib.compress(b"calendar") + b"trailing"),
])
def test_unsupported_or_incomplete_compression(encoding, body):
    with pytest.raises((source.CalendarSourceError, zlib.error)):
        source._read_body(body_response(body, {"Content-Encoding": encoding}), 128, float("inf"))


def test_compressed_wire_size_is_also_limited():
    # Empty gzip members consume wire bytes without adding decoded output.
    response = body_response(gzip.compress(b"") * 10, {"Content-Encoding": "gzip"})
    with pytest.raises(source.CalendarSourceError):
        source._read_body(response, 128, float("inf"))


def test_decompression_passes_a_bounded_output_size(monkeypatch):
    real_factory = zlib.decompressobj
    lengths = []

    class Decoder:
        def __init__(self, *args):
            self.decoder = real_factory(*args)

        def __getattr__(self, name):
            return getattr(self.decoder, name)

        def decompress(self, data, max_length):
            lengths.append(max_length)
            return self.decoder.decompress(data, max_length)

    monkeypatch.setattr(source.zlib, "decompressobj", Decoder)
    response = body_response(gzip.compress(b"x" * 1000000), {"Content-Encoding": "gzip"})
    with pytest.raises(source.CalendarSourceError):
        source._read_body(response, 2048, float("inf"))
    assert lengths and all(0 < limit <= 2049 for limit in lengths)


def test_shared_deadline_expires_between_chunks(monkeypatch):
    now = [0]
    monkeypatch.setattr(source.time, "monotonic", lambda: now[0])
    response = body_response(b"calendar")

    def read(*_args, **_kwargs):
        now[0] += 2
        return b"x"

    response.read1.side_effect = read
    with pytest.raises(source.CalendarDownloadTimeout):
        source._read_body(response, 128, 3)
    assert response.read1.call_count == 2


@pytest.mark.parametrize("stage", ["dns", "headers", "redirect", "body"])
def test_download_deadline_covers_all_hops_and_closes_connections(monkeypatch, stage):
    now = [0]
    monkeypatch.setattr(source.time, "monotonic", lambda: now[0])

    def resolve(*_args, **_kwargs):
        if stage == "dns":
            now[0] = 61
        return "93.184.216.34"

    monkeypatch.setattr(source, "_resolve", resolve)
    pool = Mock()

    def request(*_args, **_kwargs):
        now[0] += 31 if stage == "redirect" else (61 if stage == "headers" else 0)
        if stage == "redirect":
            return body_response(b"", {"Location": "/next"})
        result = body_response(b"calendar")
        if stage == "body":
            def read(*_args, **_kwargs):
                now[0] = 61
                return b"calendar"
            result.read1.side_effect = read
        return result

    responses = []

    def recorded_request(*args, **kwargs):
        result = request(*args, **kwargs)
        if stage == "redirect":
            result.status = 302
        responses.append(result)
        return result

    pool.urlopen.side_effect = recorded_request
    factory = Mock(return_value=pool)
    monkeypatch.setattr(source.urllib3, "HTTPSConnectionPool", factory)
    with pytest.raises(source.CalendarDownloadTimeout):
        source.download_calendar("https://example.com/calendar.ics")
    assert pool.urlopen.call_count == (0 if stage == "dns" else 2 if stage == "redirect" else 1)
    assert pool.close.call_count == pool.urlopen.call_count
    for result in responses:
        result.close.assert_called_once()


def test_real_chunked_and_gzip_responses_are_limited():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            size = 129 if self.path.endswith("large") else 128
            self.send_response(200)
            if self.path.startswith("/gzip"):
                body = gzip.compress(b"x" * size)
                self.send_header("Content-Encoding", "gzip")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                for part in [b"x" * 64, b"x" * (size - 64)]:
                    self.wfile.write(f"{len(part):X}\r\n".encode() + part + b"\r\n")
                self.wfile.write(b"0\r\n\r\n")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        for path in ["/chunked", "/gzip"]:
            url = f"http://127.0.0.1:{server.server_port}{path}"
            assert source.download_calendar(url, allow_private_network=True, max_bytes=128) == "x" * 128
            with pytest.raises(source.CalendarSourceError):
                source.download_calendar(url + "-large", allow_private_network=True, max_bytes=128)
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
