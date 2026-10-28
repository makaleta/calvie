import socket, ssl, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import pytest
import httpx2
import calendar_source as source


def dns_answer(ip):
    return (
        socket.AF_INET6 if ":" in ip else socket.AF_INET,
        socket.SOCK_STREAM,
        6,
        "",
        (ip, 80),
    )


@pytest.fixture
def network(monkeypatch):
    requests = []

    async def lookup(host, port):
        return [dns_answer("93.184.216.34")]

    monkeypatch.setattr(source, "_lookup", lookup)
    real = httpx2.AsyncClient

    def handler(request):
        requests.append(request)
        return httpx2.Response(200, stream=httpx2.ByteStream(b"calendar"))

    monkeypatch.setattr(
        source.httpx2,
        "AsyncClient",
        lambda **kw: real(transport=httpx2.MockTransport(handler), **kw),
    )
    return requests


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/private.ics",
        "http://10.0.0.1/private.ics",
        "http://169.254.169.254/private.ics",
        "http://192.168.1.1/private.ics",
        "http://172.16.0.1/private.ics",
        "http://0.0.0.0/private.ics",
        "http://224.0.0.1/private.ics",
        "http://[::1]/private.ics",
        "http://[fe80::1]/private.ics",
        "http://[fc00::1]/private.ics",
        "http://[::ffff:127.0.0.1]/private.ics",
        "http://[::ffff:8.8.8.8]/private.ics",
        "http://[2002:7f00:1::]/private.ics",
        "http://[64:ff9b::7f00:1]/private.ics",
    ],
)
@pytest.mark.asyncio
async def test_private_literals(network, url):
    with pytest.raises(source.CalendarSourceError):
        await source.download_calendar(url)
    assert not network


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/calendar.ics",
        "file:///calendar.ics",
        "https://user:password@example.com/calendar.ics",
        "https://example.com:0/calendar.ics",
        "https://example.com:65536/calendar.ics",
        "https://example.com:/calendar.ics#",
        "https://example.com/internal#calendar.ics",
        "https://example.com/calendar.ics\n",
        "https://example.com/\\@127.0.0.1/calendar.ics",
        "https://example.com/%zz.ics",
        "https://[fe80::1%25eth0]/calendar.ics",
        "https://%31%32%37.0.0.1/calendar.ics",
        "https://example.com/calendar.txt?x=.ics",
        "https://example.com/calendar.ics#fragment",
    ],
)
def test_invalid_direct_urls(url):
    assert not source.is_ics_url(url)


def test_query_token_on_real_ics_path_is_supported():
    assert source.is_ics_url("https://example.com/calendar.ics?token=test")


@pytest.mark.asyncio
async def test_checked_ip_and_original_identity(network, monkeypatch):
    calls = []

    async def lookup(host, port):
        calls.append(host)
        return [dns_answer("93.184.216.34" if len(calls) == 1 else "127.0.0.1")]

    monkeypatch.setattr(source, "_lookup", lookup)
    assert (
        await source.download_calendar("https://example.com:8443/calendar.ics?token=x")
        == "calendar"
    )
    assert calls == ["example.com"]
    assert network[0].url.host == "93.184.216.34"
    assert network[0].headers["host"] == "example.com:8443"
    assert network[0].extensions["sni_hostname"] == "example.com"


@pytest.mark.asyncio
@pytest.mark.parametrize("ips", [["127.0.0.1"], ["93.184.216.34", "10.0.0.1"], []])
async def test_dns_answers(network, monkeypatch, ips):
    async def lookup(*args):
        return [dns_answer(ip) for ip in ips]

    monkeypatch.setattr(source, "_lookup", lookup)
    with pytest.raises(source.CalendarSourceError):
        await source.download_calendar("http://host/calendar.ics")
    assert not network


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "location,allowed",
    [
        ("/feed", True),
        ("http://127.0.0.1/feed", False),
        ("https://internal:8081/feed", False),
        ("https://other/feed", False),
    ],
)
async def test_private_redirect_origin(monkeypatch, location, allowed):
    real = httpx2.AsyncClient
    seen = []

    async def lookup(*args):
        return [dns_answer("10.0.0.1")]

    monkeypatch.setattr(source, "_lookup", lookup)

    def handler(req):
        seen.append(req)
        return (
            httpx2.Response(302, headers={"Location": location})
            if len(seen) == 1
            else httpx2.Response(200, stream=httpx2.ByteStream(b"calendar"))
        )

    monkeypatch.setattr(
        source.httpx2,
        "AsyncClient",
        lambda **kw: real(transport=httpx2.MockTransport(handler), **kw),
    )
    if allowed:
        assert (
            await source.download_calendar(
                "https://internal/calendar", allow_private_network=True
            )
            == "calendar"
        )
    else:
        with pytest.raises(source.CalendarSourceError):
            await source.download_calendar(
                "https://internal/calendar", allow_private_network=True
            )
        assert len(seen) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "location", ["http://example.com/feed", "/feed#bad", "file:///etc/passwd", "/loop"]
)
async def test_unsafe_redirects_or_loop(monkeypatch, location):
    real = httpx2.AsyncClient
    seen = []

    async def lookup(*args):
        return [dns_answer("93.184.216.34")]

    monkeypatch.setattr(source, "_lookup", lookup)

    def handler(req):
        seen.append(req)
        return httpx2.Response(302, headers={"Location": location})

    monkeypatch.setattr(
        source.httpx2,
        "AsyncClient",
        lambda **kw: real(transport=httpx2.MockTransport(handler), **kw),
    )
    with pytest.raises(source.CalendarSourceError):
        await source.download_calendar("https://example.com/calendar.ics")
    assert len(seen) == (6 if location == "/loop" else 1)


@pytest.fixture
def tls_calendar_server():
    """Use an intentionally public test key, with no external DNS or network access."""
    pem = Path(__file__).parent / "fixtures" / "calendar-test.pem"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            self.server.received_hosts.append(self.headers["Host"])
            self.send_response(200)
            self.send_header("Content-Length", "8")
            self.end_headers()
            self.wfile.write(b"calendar")

    class Server(ThreadingHTTPServer):
        def handle_error(self, request, client_address):
            import sys

            if isinstance(sys.exception(), ConnectionResetError):
                # A rejected certificate can close the socket before the handler reads.
                return
            super().handle_error(request, client_address)

    server = Server(("127.0.0.1", 0), Handler)
    server.received_hosts = []
    server.received_sni = []
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(pem)
    context.set_servername_callback(
        lambda _sock, name, _ctx: server.received_sni.append(name)
    )
    server.socket = context.wrap_socket(server.socket, server_side=True)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield server, pem
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "hostname,trusted,success",
    [
        ("calendar.example", True, True),
        ("wrong.example", True, False),
        ("calendar.example", False, False),
    ],
)
async def test_real_tls_identity(
    monkeypatch, tls_calendar_server, hostname, trusted, success
):
    server, pem = tls_calendar_server

    async def lookup(host, port):
        return [dns_answer("127.0.0.1")]

    monkeypatch.setattr(source, "_lookup", lookup)
    if trusted:
        real = httpx2.AsyncClient
        monkeypatch.setattr(
            source.httpx2,
            "AsyncClient",
            lambda **kw: real(verify=ssl.create_default_context(cafile=str(pem)), **kw),
        )
    if success:
        assert (
            await source.download_calendar(
                f"https://{hostname}:{server.server_port}/calendar.ics",
                allow_private_network=True,
            )
            == "calendar"
        )
        assert server.received_hosts == [f"{hostname}:{server.server_port}"]
    else:
        with pytest.raises(httpx2.ConnectError):
            await source.download_calendar(
                f"https://{hostname}:{server.server_port}/calendar.ics",
                allow_private_network=True,
            )
    assert server.received_sni == [hostname]


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["headers", "body", "redirect"])
async def test_total_timeout_on_real_slow_server(phase):
    import asyncio

    tasks = set()

    async def serve(reader, writer):
        task = asyncio.current_task()
        tasks.add(task)
        try:
            await reader.readuntil(b"\r\n\r\n")
            if phase == "redirect":
                await asyncio.sleep(0.08)
                writer.write(
                    b"HTTP/1.1 302 Found\r\nLocation: /next\r\nContent-Length: 0\r\n\r\n"
                )
                await writer.drain()
            else:
                prefix = (
                    b"HTTP/1.1 200 OK\r\nX-Slow: "
                    if phase == "headers"
                    else b"HTTP/1.1 200 OK\r\nContent-Length: 9999\r\n\r\n"
                )
                writer.write(prefix)
                await writer.drain()
                while True:
                    writer.write(b"x")
                    await writer.drain()
                    await asyncio.sleep(0.02)
        except ConnectionError, asyncio.CancelledError:
            pass
        finally:
            writer.close()
            await writer.wait_closed()
            tasks.discard(task)

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    try:
        with pytest.raises(source.CalendarDownloadTimeout):
            await source.download_calendar(
                f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}/feed",
                allow_private_network=True,
                timeout=0.15,
            )
    finally:
        server.close()
        await server.wait_closed()
        for task in tuple(tasks):
            task.cancel()
        await asyncio.gather(*tuple(tasks), return_exceptions=True)


@pytest.mark.asyncio
async def test_total_timeout_includes_dns(monkeypatch):
    import anyio

    stopped = []

    async def lookup(*args):
        try:
            await anyio.sleep_forever()
        finally:
            stopped.append(True)

    monkeypatch.setattr(source, "_lookup", lookup)
    with pytest.raises(source.CalendarDownloadTimeout):
        await source.download_calendar("https://example.com/calendar.ics", timeout=0.05)
    assert stopped


@pytest.mark.asyncio
async def test_clients_closed_and_identity_not_shared(monkeypatch):
    clients = []
    requests = []
    real = httpx2.AsyncClient

    async def lookup(*args):
        return [dns_answer("93.184.216.34")]

    monkeypatch.setattr(source, "_lookup", lookup)

    def handler(req):
        requests.append(req)
        if len(requests) == 1:
            return httpx2.Response(
                302,
                headers={
                    "Location": "https://second.example/feed",
                    "Set-Cookie": "secret=x",
                },
            )
        return httpx2.Response(200, stream=httpx2.ByteStream(b"calendar"))

    def client(**kw):
        instance = real(transport=httpx2.MockTransport(handler), **kw)
        clients.append(instance)
        return instance

    monkeypatch.setattr(source.httpx2, "AsyncClient", client)
    assert (
        await source.download_calendar("https://first.example/calendar.ics")
        == "calendar"
    )
    assert len(clients) == 2 and all(c.is_closed for c in clients)
    assert [r.extensions["sni_hostname"] for r in requests] == [
        "first.example",
        "second.example",
    ]
    assert "cookie" not in requests[1].headers


@pytest.mark.asyncio
async def test_download_cancel_releases_application_capacity(monkeypatch):
    import asyncio, anyio, main
    from unittest.mock import AsyncMock
    import httpx2 as http

    started = anyio.Event()
    closed = []

    async def blocked(*args, **kwargs):
        started.set()
        try:
            await anyio.sleep_forever()
        finally:
            closed.append(True)

    monkeypatch.setattr(main, "download_calendar", blocked)
    monkeypatch.setattr(main, "calendar_slots", anyio.CapacityLimiter(1))
    processor = AsyncMock(return_value=b"[]")
    monkeypatch.setattr(main, "process_calendar", processor)
    main.CONFIG["test"] = {"url": "https://example.com/feed"}
    async with http.AsyncClient(
        transport=http.ASGITransport(app=main.app), base_url="http://test"
    ) as client:
        job = asyncio.create_task(client.get("/cal/test"))
        await started.wait()
        assert (await client.get("/")).status_code == 418
        assert (await client.get("/iframe/test")).status_code == 503
        job.cancel()
        with pytest.raises(asyncio.CancelledError):
            await job
        await asyncio.gather(*tuple(main.calendar_jobs), return_exceptions=True)
        assert closed and main.calendar_slots.borrowed_tokens == 0
        processor.assert_not_called()
        monkeypatch.setattr(
            main, "download_calendar", AsyncMock(return_value="calendar")
        )
        assert (await client.get("/cal/test")).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "host", ["2130706433", "127.1", "0177.0.0.1", "0x7f000001", "localhost"]
)
async def test_alias_addresses_cannot_bypass_dns_policy(network, monkeypatch, host):
    async def lookup(*args):
        return [dns_answer("127.0.0.1")]

    monkeypatch.setattr(source, "_lookup", lookup)
    with pytest.raises(source.CalendarSourceError):
        await source.download_calendar(f"http://{host}/calendar.ics")
    assert not network


@pytest.mark.asyncio
async def test_redirect_rechecks_same_hostname(monkeypatch):
    real = httpx2.AsyncClient
    lookups = []
    requests = []

    async def lookup(*args):
        lookups.append(True)
        return [dns_answer("93.184.216.34" if len(lookups) == 1 else "127.0.0.1")]

    monkeypatch.setattr(source, "_lookup", lookup)

    def handler(req):
        requests.append(req)
        return httpx2.Response(302, headers={"Location": "/next"})

    monkeypatch.setattr(source.httpx2, "AsyncClient",
                        lambda **kwargs: real(transport=httpx2.MockTransport(handler), **kwargs))
    with pytest.raises(source.CalendarSourceError):
        await source.download_calendar("https://example.com/calendar.ics")
    assert len(requests) == 1 and len(lookups) == 2


def test_private_default_option_remains_forbidden(monkeypatch):
    import configparser, runpy

    def configured_read(config, *args, **kwargs):
        config["DEFAULT"]["allow private network"] = "true"

    monkeypatch.setattr(configparser.ConfigParser, "read", configured_read)
    with pytest.raises(ValueError, match="individual calendar"):
        runpy.run_module("main", run_name="__test_policy__")
