"""Bounded calendar work, including cancellation and actual compressed streams."""

import asyncio
import configparser
import gzip
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import runpy
import threading
from unittest.mock import AsyncMock, Mock
import zlib

import anyio
from fastapi import HTTPException
from fastapi.testclient import TestClient
import httpx2
import pytest

import calendar_source as source
import main


@pytest.fixture
def calendar(monkeypatch):
    main.CONFIG["test"] = {"url": "https://example.com/SECRET.ics"}
    download = AsyncMock(return_value="calendar")
    parser = AsyncMock(return_value=b"[]")
    monkeypatch.setattr(main, "download_calendar", download)
    monkeypatch.setattr(main, "process_calendar", parser)
    monkeypatch.setattr(main, "calendar_slots", anyio.CapacityLimiter(4))
    return download, parser


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
    assert calendar[1].call_args.args[0]["end"]


@pytest.mark.parametrize("route", ["cal", "iframe"])
def test_global_limits_cannot_be_increased_by_calendar_or_query(calendar, route):
    main.CONFIG["test"]["max days"] = "1000"
    main.CONFIG["test"]["max events"] = "10000"
    main.CONFIG["test"]["max calendar bytes"] = "99999999"
    client = TestClient(main.app)
    assert client.get(f"/{route}/test?days=367&max_days=1000").status_code == 422
    assert client.get(f"/{route}/test?max_calendar_bytes=99999999").status_code == 200
    calendar[0].assert_called_once_with(
        "https://example.com/SECRET.ics",
        allow_private_network=False,
        max_bytes=5242880,
        timeout=60,
    )
    assert calendar[1].call_args.args[0]["max_events"] == 5000


@pytest.mark.parametrize("route", ["cal", "iframe"])
@pytest.mark.parametrize(
    "error",
    [
        source.CalendarDownloadTimeout("SECRET"),
        source.CalendarDownloadTimeout("SECRET"),
    ],
)
def test_timeout_status_and_redaction_release_slot(calendar, route, error):
    calendar[0].side_effect = error
    result = TestClient(main.app).get(f"/{route}/test")
    assert result.status_code == 504
    assert "SECRET" not in result.text
    assert main.calendar_slots.borrowed_tokens == 0


@pytest.mark.parametrize(
    "option",
    [
        "max calendar bytes",
        "max days",
        "max events",
        "download timeout",
        "max concurrent calendars",
        "parser timeout",
        "parser memory mib",
        "max response bytes",
    ],
)
@pytest.mark.parametrize("value", ["0", "-1", "1.5", "invalid", ""])
def test_invalid_global_limits_fail_at_startup(option, value, monkeypatch):
    def configured_read(config, *_args, **_kwargs):
        config["DEFAULT"][option] = value

    monkeypatch.setattr(configparser.ConfigParser, "read", configured_read)
    with pytest.raises(ValueError, match="positive integer in DEFAULT"):
        runpy.run_module("main", run_name="__test_limits__")


def test_administrator_can_change_global_limits():
    for key, value in {
        "max calendar bytes": "1024",
        "max days": "10",
        "max events": "20",
        "download timeout": "2",
        "max concurrent calendars": "1",
    }.items():
        main.CONFIG["DEFAULT"][key] = value
    limits = main.ResourceLimits.from_config()
    assert limits == main.ResourceLimits(1024, 10, 20, 2, 1, 10, 256, 20971520)


def body_response(body, headers=None):
    stream = io.BytesIO(body)
    result = Mock(headers=headers or {}, status=200)

    async def chunks(chunk_size=65536):
        while chunk := stream.read(chunk_size):
            yield chunk

    result.aiter_raw = chunks
    return result


@pytest.mark.parametrize(
    "size,accepted", [(0, False), (127, True), (128, True), (129, False)]
)
@pytest.mark.asyncio
async def test_body_size_boundaries(size, accepted):
    response = body_response(b"x" * size)
    if accepted:
        assert await source._read_body(response, 128) == b"x" * size
    else:
        with pytest.raises(source.CalendarSourceError):
            await source._read_body(response, 128)


@pytest.mark.parametrize("length", ["129", "-1", "invalid", "1, 2"])
@pytest.mark.asyncio
async def test_reject_content_length_before_read(length):
    response = body_response(b"x", {"Content-Length": length})
    with pytest.raises(source.CalendarSourceError):
        await source._read_body(response, 128)


@pytest.mark.parametrize(
    "headers", [{}, {"Content-Length": "1"}, {"Transfer-Encoding": "chunked"}]
)
@pytest.mark.asyncio
async def test_count_actual_bytes_regardless_of_length_header(headers):
    response = body_response(b"x" * 129, headers)
    with pytest.raises(source.CalendarSourceError):
        await source._read_body(response, 128)


def raw_deflate(data):
    compressor = zlib.compressobj(wbits=-zlib.MAX_WBITS)
    return compressor.compress(data) + compressor.flush()


@pytest.mark.parametrize(
    "encoding,compress",
    [("gzip", gzip.compress), ("deflate", zlib.compress), ("deflate", raw_deflate)],
)
@pytest.mark.parametrize("size", [128, 129, 1000000])
@pytest.mark.asyncio
async def test_bounded_decompression(encoding, compress, size):
    data = b"x" * size
    response = body_response(compress(data), {"Content-Encoding": encoding})
    if size <= 128:
        assert await source._read_body(response, 128) == data
    else:
        with pytest.raises(source.CalendarSourceError):
            await source._read_body(response, 128)


@pytest.mark.asyncio
async def test_gzip_members_and_split_deflate_header():
    data = gzip.compress(b"first") + gzip.compress(b"second")
    assert (
        await source._read_body(body_response(data, {"Content-Encoding": "gzip"}), 128)
        == b"firstsecond"
    )
    data = zlib.compress(b"calendar")
    response = body_response(b"", {"Content-Encoding": "deflate"})

    async def split(**_kwargs):
        for part in [data[:1], data[1:2], data[2:]]:
            yield part

    response.aiter_raw = split
    assert await source._read_body(response, 128) == b"calendar"


@pytest.mark.parametrize(
    "encoding,body",
    [
        ("br", b"data"),
        ("gzip, deflate", b"data"),
        ("gzip", b"invalid"),
        ("gzip", gzip.compress(b"calendar")[:-1]),
        ("deflate", zlib.compress(b"calendar")[:-1]),
        ("deflate", zlib.compress(b"calendar") + b"trailing"),
    ],
)
@pytest.mark.asyncio
async def test_unsupported_or_incomplete_compression(encoding, body):
    with pytest.raises((source.CalendarSourceError, zlib.error)):
        await source._read_body(
            body_response(body, {"Content-Encoding": encoding}), 128
        )


@pytest.mark.asyncio
async def test_compressed_wire_size_is_also_limited():
    # Empty gzip members consume wire bytes without adding decoded output.
    response = body_response(gzip.compress(b"") * 10, {"Content-Encoding": "gzip"})
    with pytest.raises(source.CalendarSourceError):
        await source._read_body(response, 128)


@pytest.mark.asyncio
async def test_decompression_passes_a_bounded_output_size(monkeypatch):
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
    response = body_response(
        gzip.compress(b"x" * 1000000), {"Content-Encoding": "gzip"}
    )
    with pytest.raises(source.CalendarSourceError):
        await source._read_body(response, 2048)
    assert lengths and all(0 < limit <= 2049 for limit in lengths)


@pytest.mark.asyncio
async def test_real_chunked_and_gzip_responses_are_limited():
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
    worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    worker.start()
    try:
        for path in ["/chunked", "/gzip"]:
            url = f"http://127.0.0.1:{server.server_port}{path}"
            assert (
                await source.download_calendar(
                    url, allow_private_network=True, max_bytes=128
                )
                == "x" * 128
            )
            with pytest.raises(source.CalendarSourceError):
                await source.download_calendar(
                    url + "-large", allow_private_network=True, max_bytes=128
                )
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
