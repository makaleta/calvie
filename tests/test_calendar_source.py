"""Exercise the network boundary with deterministic DNS and HTTP responses."""

import configparser
import runpy
import socket
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient
import urllib3

import calendar_source as source
import main


def dns_answer(address):
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    return family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, 80)


def response(status=200, headers=None, body=b"calendar"):
    result = Mock(status=status, headers=headers or {})
    result.read.return_value = body
    return result


@pytest.fixture
def network(monkeypatch):
    dns = Mock(return_value=[dns_answer("93.184.216.34")])
    pool = Mock()
    pool.urlopen.return_value = response()
    http = Mock(return_value=pool)
    https = Mock(return_value=pool)
    monkeypatch.setattr(source.socket, "getaddrinfo", dns)
    monkeypatch.setattr(source.urllib3, "HTTPConnectionPool", http)
    monkeypatch.setattr(source.urllib3, "HTTPSConnectionPool", https)
    return dns, pool, http, https


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/private.ics", "http://10.0.0.1/private.ics",
    "http://169.254.169.254/private.ics", "http://192.168.1.1/private.ics",
    "http://172.16.0.1/private.ics", "http://0.0.0.0/private.ics",
    "http://224.0.0.1/private.ics", "http://[::1]/private.ics",
    "http://[fe80::1]/private.ics", "http://[fc00::1]/private.ics",
    "http://[::ffff:127.0.0.1]/private.ics", "http://[::ffff:8.8.8.8]/private.ics",
    "http://[2002:7f00:1::]/private.ics", "http://[64:ff9b::7f00:1]/private.ics",
])
def test_private_and_special_literal_addresses_are_blocked(network, url):
    dns, pool, http, https = network
    with pytest.raises(source.CalendarSourceError):
        source.download_calendar(url)
    dns.assert_not_called()
    http.assert_not_called()
    https.assert_not_called()


@pytest.mark.parametrize("addresses", [
    ["127.0.0.1"], ["10.0.0.1"], ["::1"],
    ["93.184.216.34", "192.168.1.1"],
    ["2606:4700:4700::1111", "::1"], [],
])
def test_all_dns_answers_must_be_public(network, addresses):
    dns, pool, http, https = network
    dns.return_value = [dns_answer(address) for address in addresses]
    with pytest.raises(source.CalendarSourceError):
        source.download_calendar("https://example.com/calendar.ics")
    https.assert_not_called()


@pytest.mark.parametrize("host", ["2130706433", "127.1", "0177.0.0.1", "0x7f000001", "localhost"])
def test_alternate_address_spellings_checked_after_resolution(network, host):
    dns, pool, http, https = network
    dns.return_value = [dns_answer("127.0.0.1")]
    with pytest.raises(source.CalendarSourceError):
        source.download_calendar(f"http://{host}/calendar.ics")
    http.assert_not_called()


def test_connection_pins_checked_ip_preserving_https_identity(network):
    dns, pool, http, https = network
    # A second lookup would return loopback, but is never used.
    dns.side_effect = [[dns_answer("93.184.216.34")], [dns_answer("127.0.0.1")]]
    assert source.download_calendar("https://example.com:8443/calendar.ics?token=test") == "calendar"
    dns.assert_called_once_with("example.com", 8443, type=socket.SOCK_STREAM)
    args, kwargs = https.call_args
    assert args == ("93.184.216.34", 8443)
    assert kwargs["server_hostname"] == kwargs["assert_hostname"] == "example.com"
    assert kwargs["cert_reqs"] == "CERT_REQUIRED"
    assert kwargs["retries"] is False
    assert kwargs["timeout"].connect_timeout == 5
    assert kwargs["timeout"].read_timeout == 10
    pool.urlopen.assert_called_once_with(
        "GET", "/calendar.ics?token=test", headers={"Host": "example.com:8443"},
        redirect=False, retries=False, preload_content=False,
    )
    pool.urlopen.return_value.close.assert_called_once()
    pool.close.assert_called_once()


@pytest.mark.parametrize("url,host,port,target", [
    ("http://example.com:8080/calendar.ics", "example.com:8080", 8080, "/calendar.ics"),
    ("example.com/calendar.ics", "example.com", 80, "/calendar.ics"),
    ("calendar.ics", "calendar.ics", 80, "/"),
])
def test_http_and_legacy_schemeless_urls(network, url, host, port, target):
    dns, pool, http, https = network
    assert source.is_ics_url(url)
    source.download_calendar(url)
    assert http.call_args.args == ("93.184.216.34", port)
    assert pool.urlopen.call_args.args == ("GET", target)
    assert pool.urlopen.call_args.kwargs["headers"] == {"Host": host}


def test_public_ipv6_preserves_bracketed_host(network):
    dns, pool, http, https = network
    source.download_calendar("http://[2606:4700:4700::1111]:8080/calendar.ics")
    dns.assert_not_called()
    assert http.call_args.args == ("2606:4700:4700::1111", 8080)
    assert pool.urlopen.call_args.kwargs["headers"] == {"Host": "[2606:4700:4700::1111]:8080"}


@pytest.mark.parametrize("url", [
    "ftp://example.com/calendar.ics", "file:///calendar.ics",
    "https://user:password@example.com/calendar.ics", "https://example.com:0/calendar.ics",
    "https://example.com:65536/calendar.ics", "https://example.com:/calendar.ics#",
    "https://example.com/internal#calendar.ics", "https://example.com/calendar.ics\n",
    "https://example.com/\\@127.0.0.1/calendar.ics", "https://example.com/%zz.ics",
    "https://[fe80::1%25eth0]/calendar.ics", "https://%31%32%37.0.0.1/calendar.ics",
    "https://example.com/calendar.txt?x=.ics", "https://example.com/calendar.ics#fragment",
])
def test_invalid_direct_urls(url):
    assert not source.is_ics_url(url)


def test_query_token_on_real_ics_path_is_supported():
    assert source.is_ics_url("https://example.com/calendar.ics?token=test")


@pytest.mark.parametrize("target", ["http://127.0.0.1/internal", "http://[::1]/internal"])
def test_redirect_to_private_target_is_blocked(network, target):
    dns, pool, http, https = network
    first = response(302, {"Location": target})
    pool.urlopen.return_value = first
    with pytest.raises(source.CalendarSourceError):
        source.download_calendar("http://example.com/calendar.ics")
    assert pool.urlopen.call_count == 1
    first.close.assert_called_once()
    pool.close.assert_called_once()


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_relative_redirect_and_extensionless_target(network, status):
    dns, pool, http, https = network
    first = response(status, {"Location": "../feed?token=test"})
    last = response(body=b"caf\xe9\r\n", headers={"Content-Type": 'text/calendar; charset="iso-8859-1"'})
    pool.urlopen.side_effect = [first, last]
    assert source.download_calendar("https://example.com/path/calendar.ics") == "caf\u00e9\n"
    assert pool.urlopen.call_args.args == ("GET", "/feed?token=test")
    assert dns.call_count == 2
    first.close.assert_called_once()
    last.close.assert_called_once()
    assert pool.close.call_count == 2


def test_dns_rechecked_for_redirect_to_same_hostname(network):
    dns, pool, http, https = network
    dns.side_effect = [[dns_answer("93.184.216.34")], [dns_answer("127.0.0.1")]]
    pool.urlopen.return_value = response(302, {"Location": "/next"})
    with pytest.raises(source.CalendarSourceError):
        source.download_calendar("https://example.com/calendar.ics")
    assert pool.urlopen.call_count == 1


def test_https_downgrade_rejected_before_connection(network):
    dns, pool, http, https = network
    pool.urlopen.return_value = response(302, {"Location": "http://example.com/feed"})
    with pytest.raises(source.CalendarSourceError):
        source.download_calendar("https://example.com/calendar.ics")
    http.assert_not_called()


def test_five_redirects_allowed_but_sixth_rejected(network):
    dns, pool, http, https = network
    pool.urlopen.side_effect = [response(302, {"Location": "/next"}) for _ in range(5)] + [response()]
    assert source.download_calendar("https://example.com/calendar.ics") == "calendar"
    pool.urlopen.reset_mock(side_effect=True)
    pool.urlopen.side_effect = [response(302, {"Location": "/loop"}) for _ in range(6)]
    with pytest.raises(source.CalendarSourceError):
        source.download_calendar("https://example.com/calendar.ics")
    assert pool.urlopen.call_count == 6


@pytest.mark.parametrize("location", [None, "file:///etc/passwd", "//127.0.0.1/feed", "/feed#fragment", "\n/feed"])
def test_invalid_redirect(network, location):
    dns, pool, http, https = network
    pool.urlopen.return_value = response(302, {"Location": location})
    with pytest.raises(source.CalendarSourceError):
        source.download_calendar("http://example.com/calendar.ics")
    assert pool.urlopen.call_count == 1


def test_private_opt_in_allows_original_origin_redirect(network):
    dns, pool, http, https = network
    dns.return_value = [dns_answer("10.0.0.1")]
    pool.urlopen.side_effect = [response(302, {"Location": "/feed"}), response()]
    assert source.download_calendar("http://internal:8080/calendar", allow_private_network=True) == "calendar"
    assert http.call_args.args == ("10.0.0.1", 8080)
    assert pool.urlopen.call_count == 2


@pytest.mark.parametrize("location", ["http://other:8080/feed", "http://internal:8081/feed", "https://internal:8080/feed"])
def test_private_exception_does_not_follow_changed_origin(network, location):
    dns, pool, http, https = network
    dns.return_value = [dns_answer("10.0.0.1")]
    pool.urlopen.return_value = response(302, {"Location": location})
    with pytest.raises(source.CalendarSourceError):
        source.download_calendar("http://internal:8080/calendar", allow_private_network=True)
    assert pool.urlopen.call_count == 1


def test_private_source_can_redirect_to_validated_public_origin(network):
    dns, pool, http, https = network
    dns.side_effect = [[dns_answer("10.0.0.1")], [dns_answer("93.184.216.34")]]
    pool.urlopen.side_effect = [response(302, {"Location": "https://example.com/feed"}), response()]
    assert source.download_calendar("http://internal/calendar", allow_private_network=True) == "calendar"
    assert https.call_args.args == ("93.184.216.34", 443)


@pytest.mark.parametrize("failure", ["request", "read", "decode", "empty", "http"])
def test_connections_closed_on_failure(network, failure):
    dns, pool, http, https = network
    result = pool.urlopen.return_value
    if failure == "request":
        pool.urlopen.side_effect = urllib3.exceptions.SSLError("test certificate failure")
    elif failure == "read":
        result.read.side_effect = urllib3.exceptions.ReadTimeoutError(None, "/", "timeout")
    elif failure == "decode":
        result.read.return_value = b"\xff"
    elif failure == "empty":
        result.read.return_value = b""
    else:
        result.status = 500
    with pytest.raises(Exception):
        source.download_calendar("https://example.com/calendar.ics")
    pool.close.assert_called_once()
    if failure != "request":
        result.close.assert_called_once()


@pytest.mark.parametrize("route", ["cal", "iframe"])
@pytest.mark.parametrize("stage", ["download", "parser"])
def test_errors_do_not_disclose_private_calendar_url(route, stage):
    main.CONFIG["private"] = {"url": "https://example.com/SECRET_TOKEN.ics"}
    download = Mock(return_value="calendar")
    parser = Mock(return_value=[])
    (download if stage == "download" else parser).side_effect = ValueError("https://example.com/SECRET_TOKEN.ics")
    with patch("main.download_calendar", download), patch("main.events", parser):
        result = TestClient(main.app).get(f"/{route}/private")
    assert result.status_code == 400
    assert "SECRET_TOKEN" not in result.text
    assert "example.com" not in result.text
    assert "Unable to load calendar" in result.text


def test_private_option_only_for_named_calendar():
    main.CONFIG["internal"] = {"url": "http://127.0.0.1/calendar", "allow private network": "true"}
    with patch("main.download_calendar", return_value="calendar") as download, patch("main.events", return_value=[]):
        client = TestClient(main.app)
        assert client.get("/cal/internal").status_code == 200
        download.assert_called_with("http://127.0.0.1/calendar", allow_private_network=True)
        download.side_effect = source.CalendarSourceError("blocked")
        assert client.get("/cal/http://127.0.0.1/calendar.ics?allow_private_network=true").status_code == 400
        download.assert_called_with("http://127.0.0.1/calendar.ics", allow_private_network=False)


@pytest.mark.parametrize("route", ["cal", "iframe"])
def test_routes_use_guarded_download_with_real_parser(network, route):
    dns, pool, http, https = network
    pool.urlopen.return_value = response(body=b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nEND:VCALENDAR\r\n")
    client = TestClient(main.app)
    assert client.get(f"/{route}/https://example.com/calendar.ics").status_code == 200
    assert client.get(f"/{route}/http://127.0.0.1/calendar.ics").status_code == 400
    assert pool.urlopen.call_count == 1


@pytest.mark.parametrize("route", ["cal", "iframe"])
def test_named_internal_source_needs_explicit_opt_in(network, route):
    dns, pool, http, https = network
    dns.return_value = [dns_answer("10.0.0.1")]
    pool.urlopen.return_value = response(body=b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nEND:VCALENDAR\r\n")
    main.CONFIG["internal"] = {"url": "http://internal:8080/feed"}
    client = TestClient(main.app)
    assert client.get(f"/{route}/internal").status_code == 400
    pool.urlopen.assert_not_called()
    main.CONFIG["internal"]["allow private network"] = "true"
    assert client.get(f"/{route}/internal").status_code == 200
    assert http.call_args.args == ("10.0.0.1", 8080)


def test_encoded_source_query_is_passed_to_downloader(network):
    dns, pool, http, https = network
    pool.urlopen.return_value = response(body=b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nEND:VCALENDAR\r\n")
    assert TestClient(main.app).get("/cal/https://example.com/calendar.ics%3Ftoken=test").status_code == 200
    assert pool.urlopen.call_args.args == ("GET", "/calendar.ics?token=test")


def test_default_private_option_is_rejected_at_import():
    def configured_read(config, *_args, **_kwargs):
        config.read_dict({"DEFAULT": {"allow private network": "true"}})
    with patch.object(configparser.ConfigParser, "read", configured_read):
        with pytest.raises(ValueError, match="individual calendar"):
            runpy.run_module("main", run_name="__test_main__")


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
    context.set_servername_callback(lambda _sock, name, _ctx: server.received_sni.append(name))
    server.socket = context.wrap_socket(server.socket, server_side=True)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield server, pem
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


@pytest.mark.parametrize("hostname,trusted,success", [
    ("calendar.example", True, True),
    ("wrong.example", True, False),
    ("calendar.example", False, False),
])
def test_real_tls_pinned_ip_and_certificate_verification(monkeypatch, tls_calendar_server, hostname, trusted, success):
    server, pem = tls_calendar_server
    lookup = socket.getaddrinfo
    lookups = []

    def resolve(host, port, *args, **kwargs):
        lookups.append(host)
        if host in {"calendar.example", "wrong.example"}:
            return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", port))]
        return lookup(host, port, *args, **kwargs)

    monkeypatch.setattr(source.socket, "getaddrinfo", resolve)
    if trusted:
        real_pool = urllib3.HTTPSConnectionPool
        context = ssl.create_default_context(cafile=str(pem))

        def pool(*args, **kwargs):
            return real_pool(*args, ssl_context=context, **kwargs)

        monkeypatch.setattr(source.urllib3, "HTTPSConnectionPool", pool)
    url = f"https://{hostname}:{server.server_port}/calendar.ics"
    if success:
        assert source.download_calendar(url, allow_private_network=True) == "calendar"
        assert server.received_hosts == [f"{hostname}:{server.server_port}"]
    else:
        with pytest.raises(urllib3.exceptions.SSLError):
            source.download_calendar(url, allow_private_network=True)
        assert server.received_hosts == []
    assert server.received_sni == [hostname]
    assert lookups == [hostname, "127.0.0.1"]
