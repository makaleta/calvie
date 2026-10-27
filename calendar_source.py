"""Download calendars without allowing URLs to select unchecked network targets."""

from dataclasses import dataclass
from email.message import Message
import ipaddress
import re
import socket
import time
from urllib.parse import quote, urljoin, urlsplit, urlunsplit
import zlib

import urllib3


class CalendarSourceError(ValueError):
    """A calendar source is invalid, disallowed, or unavailable."""


class CalendarDownloadTimeout(CalendarSourceError, TimeoutError):
    """The shared download time budget has expired."""


def _remaining_time(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise CalendarDownloadTimeout("Calendar download timed out")
    return remaining


def _read_body(response, max_bytes: int, deadline: float) -> bytes:
    """Bound both wire bytes and decompressed output without an unbounded flush."""
    length = response.headers.get("Content-Length")
    if length is not None:
        if not re.fullmatch(r"[0-9]+", length) or int(length) > max_bytes:
            raise CalendarSourceError("Invalid or excessive calendar length")
    encoding = response.headers.get("Content-Encoding", "").strip().lower()
    if encoding not in {"", "identity", "gzip", "deflate"}:
        raise CalendarSourceError("Unsupported calendar encoding")
    compressed = encoding in {"gzip", "deflate"}
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS) if encoding == "gzip" else None
    header = b""
    output = bytearray()
    transferred = 0
    while True:
        _remaining_time(deadline)
        chunk = response.read1(min(65536, max_bytes - transferred + 1), decode_content=False)
        _remaining_time(deadline)
        if not chunk:
            break
        transferred += len(chunk)
        if transferred > max_bytes:
            raise CalendarSourceError("Calendar is too large")
        if not compressed:
            output.extend(chunk)
            continue
        if decoder is None:
            # HTTP deflate exists in both zlib-wrapped and raw forms.
            header += chunk
            if len(header) < 2:
                continue
            wrapped = header[0] & 15 == 8 and int.from_bytes(header[:2], "big") % 31 == 0
            decoder = zlib.decompressobj(zlib.MAX_WBITS if wrapped else -zlib.MAX_WBITS)
            chunk, header = header, b""
        while chunk:
            _remaining_time(deadline)
            if decoder.eof:
                if encoding != "gzip":
                    raise CalendarSourceError("Unexpected data after compressed calendar")
                decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
            output.extend(decoder.decompress(chunk, max_bytes - len(output) + 1))
            if len(output) > max_bytes:
                raise CalendarSourceError("Calendar is too large")
            chunk = decoder.unused_data if decoder.eof else decoder.unconsumed_tail
    if compressed and (decoder is None or not decoder.eof):
        raise CalendarSourceError("Incomplete compressed calendar")
    if not output:
        raise CalendarSourceError("Calendar source is empty")
    return bytes(output)


@dataclass(frozen=True)
class CalendarURL:
    scheme: str
    host: str
    port: int
    path: str
    query: str

    @property
    def origin(self) -> tuple[str, str, int]:
        return self.scheme, self.host, self.port

    @property
    def authority(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        default_port = 443 if self.scheme == "https" else 80
        return host if self.port == default_port else f"{host}:{self.port}"

    @property
    def target(self) -> str:
        path = quote(self.path or "/", safe="/%:@!$&'()*+,;=-._~")
        query = quote(self.query, safe="/%?:@!$&'()*+,;=-._~")
        return path + (f"?{query}" if query else "")

    @property
    def url(self) -> str:
        return urlunsplit((self.scheme, self.authority, self.path, self.query, ""))


def normalize_url(value: str) -> CalendarURL:
    """Parse once, rejecting URL forms that different clients could interpret differently."""
    if not value or any(ord(c) <= 32 or ord(c) == 127 for c in value):
        raise CalendarSourceError("Invalid calendar URL")
    if "\\" in value or "#" in value or re.search(r"%(?![0-9a-fA-F]{2})", value):
        raise CalendarSourceError("Invalid calendar URL")
    if "://" not in value:
        value = "http://" + value
    try:
        parts = urlsplit(value)
        if (parts.scheme not in {"http", "https"} or not parts.hostname
                or parts.username is not None or parts.password is not None):
            raise ValueError
        host = parts.hostname
        if "%" in host:
            raise ValueError
        try:
            host = str(ipaddress.ip_address(host))
        except ValueError:
            host = host.encode("idna").decode("ascii").lower().removesuffix(".")
            if len(host) > 253 or not re.fullmatch(
                r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
                r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*", host
            ):
                raise ValueError
        port = parts.port if parts.port is not None else (443 if parts.scheme == "https" else 80)
        if not 1 <= port <= 65535:
            raise ValueError
        return CalendarURL(parts.scheme, host, port, parts.path, parts.query)
    except (ValueError, UnicodeError) as exc:
        raise CalendarSourceError("Invalid calendar URL") from exc


def is_ics_url(value: str) -> bool:
    """Recognize direct calendar URLs; this is syntax validation, not network authorization."""
    try:
        source = normalize_url(value)
    except CalendarSourceError:
        return False
    # Preserve the historical bare-host form (e.g. calendar.ics).
    return source.path.endswith(".ics") or (not source.path and source.host.endswith(".ics"))


def _public_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if (not address.is_global or address.is_multicast or address.is_reserved
            or address.is_loopback or address.is_link_local or address.is_unspecified):
        return False
    if isinstance(address, ipaddress.IPv6Address):
        # Reject transition mechanisms which may embed or translate private IPv4 targets.
        if (address.ipv4_mapped is not None or address.sixtofour is not None
                or address.teredo is not None
                or address in ipaddress.ip_network("64:ff9b::/96")):
            return False
    return True


def _resolve(source: CalendarURL, allow_private: bool) -> str:
    try:
        addresses = [ipaddress.ip_address(source.host)]
    except ValueError:
        answers = socket.getaddrinfo(source.host, source.port, type=socket.SOCK_STREAM)
        addresses = [ipaddress.ip_address(answer[4][0]) for answer in answers]
    if not addresses or (not allow_private and not all(_public_address(ip) for ip in addresses)):
        raise CalendarSourceError("Calendar destination is not allowed")
    # The connection uses this numeric address, never another lookup of the hostname.
    return str(addresses[0])


def download_calendar(url: str, *, allow_private_network: bool = False,
                      max_bytes: int = 5242880, timeout: int = 60) -> str:
    """Validate each hop and pin its connection to an already checked IP address."""
    if max_bytes < 1 or timeout <= 0:
        raise ValueError("Download limits must be positive")
    deadline = time.monotonic() + timeout
    source = normalize_url(url)
    private_origin = source.origin if allow_private_network else None
    for hop in range(6):
        _remaining_time(deadline)
        address = _resolve(source, allow_private=source.origin == private_origin)
        remaining = _remaining_time(deadline)
        options = {"timeout": urllib3.Timeout(connect=min(5, remaining), read=min(10, remaining)), "retries": False}
        if source.scheme == "https":
            pool = urllib3.HTTPSConnectionPool(
                address, source.port, server_hostname=source.host,
                assert_hostname=source.host, cert_reqs="CERT_REQUIRED", **options,
            )
        else:
            pool = urllib3.HTTPConnectionPool(address, source.port, **options)
        response = None
        try:
            response = pool.urlopen(
                "GET", source.target, headers={"Host": source.authority},
                redirect=False, retries=False, preload_content=False,
            )
            _remaining_time(deadline)
            if response.status in {301, 302, 303, 307, 308}:
                location = response.headers.get("Location")
                if (not location or hop == 5 or "\\" in location or "#" in location
                        or any(ord(c) <= 32 or ord(c) == 127 for c in location)):
                    raise CalendarSourceError("Invalid calendar redirect")
                following = normalize_url(urljoin(source.url, location))
                if source.scheme == "https" and following.scheme == "http":
                    raise CalendarSourceError("Insecure calendar redirect")
                source = following
                continue
            if not 200 <= response.status < 300:
                raise CalendarSourceError("Calendar source is unavailable")
            data = _read_body(response, max_bytes, deadline)
            content_type = Message()
            content_type["Content-Type"] = response.headers.get("Content-Type", "")
            return data.decode(content_type.get_content_charset() or "utf-8").replace("\r", "")
        finally:
            try:
                if response is not None:
                    response.close()
            finally:
                pool.close()
    raise CalendarSourceError("Too many calendar redirects")
