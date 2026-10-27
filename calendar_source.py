"""Download calendars without allowing URLs to select unchecked network targets."""

from dataclasses import dataclass
from email.message import Message
import ipaddress
import re
import socket
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

import urllib3


class CalendarSourceError(ValueError):
    """A calendar source is invalid, disallowed, or unavailable."""


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


def download_calendar(url: str, *, allow_private_network: bool = False) -> str:
    """Validate each hop and pin its connection to an already checked IP address."""
    source = normalize_url(url)
    private_origin = source.origin if allow_private_network else None
    for hop in range(6):
        address = _resolve(source, allow_private=source.origin == private_origin)
        options = {"timeout": urllib3.Timeout(connect=5, read=10), "retries": False}
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
            data = response.read()
            if not data:
                raise CalendarSourceError("Calendar source is empty")
            content_type = Message()
            content_type["Content-Type"] = response.headers.get("Content-Type", "")
            return data.decode(content_type.get_content_charset() or "utf-8").replace("\r", "")
        finally:
            if response is not None:
                response.close()
            pool.close()
    raise CalendarSourceError("Too many calendar redirects")
