# Calvie

**Calvie** is a simple iCal viewer built with FastAPI.

## Features

- View iCal events
- Simple and easy to use interface

## Requirements

- Linux (including Docker); other platforms are rejected at startup
- Python 3.14+
- FastAPI
- Uvicorn
- icalevents
- Jinja2
- Babel

## Installation

1. Clone the repository:
    ```sh
    git clone https://github.com/makaleta/calvie.git
    cd calvie
    ```

2. Install dependencies using [uv](https://docs.astral.sh/uv/getting-started/installation/):
    ```sh
    uv sync --locked
    ```
3. For named calendars, create `config.ini` in the repository root. Direct calendar URLs work without it.
Example contents:
```aiignore
[DEFAULT]
timezone = Europe/London
days to future = 40
locale = en_GB
width = 355

[exampleCal]
url = https://example.com/calendar.ics
```

## Usage

1. Run the FastAPI application:
    ```sh
    uv run --locked uvicorn main:app --reload
    ```

2. Open your browser and navigate to `http://127.0.0.1:8000`.

### Iframe color scheme

Use `color_scheme` on `/iframe/{name}` to select a theme:

- `light` or `dark`: select one theme.
- `light dark` or `dark light`: follow the browser's color preference.
- `normal`: use the default light styling without automatic dark colors.

For example: `/iframe/exampleCal?color_scheme=light%20dark`.
Without a theme parameter, the iframe follows the browser's preference.
The legacy `colour=white` and `colour=black` parameters remain supported;
`color_scheme` takes precedence when both parameters are supplied.

### Calendar source security

Direct calendar URLs and configured sources use HTTP or HTTPS and must resolve
only to public IP addresses. Nonstandard ports remain supported. URLs without
a scheme use HTTP. Direct URLs must have a path ending in `.ics` (the legacy
bare-host form `calendar.ics` also remains supported); query tokens are allowed.
URL credentials, fragments, and ambiguous URL forms are rejected.
When including a source query in a route, encode its `?` as `%3F`, for example
`/cal/https://example.com/calendar.ics%3Ftoken=example-token`.

For a trusted internal calendar, explicitly enable access in that calendar's
section of your local `config.ini`:

```ini
[internalCalendar]
url = http://calendar.internal:8080/feed
allow private network = true
```

This option is forbidden in `[DEFAULT]` and cannot be enabled through request
parameters. It permits private addresses only for the configured source's
original scheme, hostname, and port. Redirects to another origin must resolve
to public addresses. Up to five redirects are followed; HTTPS-to-HTTP redirects
are rejected. Each connection uses a validated IP while preserving the original
HTTP host and HTTPS certificate verification. No environment proxy is used.

Existing internal sources need this explicit opt-in after upgrading. Restart
the application after changing configuration. Calendar failures return generic
errors without exposing source URLs.

### Resource limits

Calendar downloads run in worker threads. Parsing, timezone conversion, and
JSON/HTML preparation run in a fresh Linux process for each request.
Both calendar routes share four loading slots per application worker. Once all
slots are occupied, new calendar requests receive HTTP 503 and `Retry-After: 1`
without queuing. An interrupted download retains its slot until the thread
finishes and does not then start a parser. An interrupted parser is killed and
reaped before its slot is released. With multiple Uvicorn workers, each worker
has its own slots.

The following global limits can be changed only in `[DEFAULT]` in `config.ini`:

```ini
[DEFAULT]
max calendar bytes = 5242880
max days = 366
max events = 5000
download timeout = 60
max concurrent calendars = 4
parser timeout = 10
parser memory mib = 256
max response bytes = 20971520
```

Every limit must be a positive integer; invalid settings prevent startup.
Per-calendar sections and HTTP parameters cannot override these global limits.
The `days` parameter and configured `days to future` must be between 1 and
`max days`; invalid ranges return HTTP 422 before downloading. The default
window remains 40 days. Results over `max events` are rejected, never truncated.

Downloads are read in blocks of at most 64 KiB. Both transferred bytes and
decompressed bytes must fit `max calendar bytes` (5 MiB by default), regardless
of `Content-Length`. Uncompressed, gzip (including concatenated members), and
deflate responses are supported; other or stacked content encodings are rejected.
Size and event-count failures return HTTP 400, and download timeouts return
HTTP 504. The iframe renders these errors with the same status and retry header.
Invalid timezone or locale also returns 422 before downloading.

The download budget covers all redirects and body reads and is checked between
operations. Connection/read timeouts remain at most 5/10 seconds, shortened when
the remaining budget is smaller. The budget cannot forcibly interrupt DNS or
an internally blocked operation, so it is not a strict wall-clock deadline.

The processing timeout covers process startup, input transfer, parsing, and
response preparation. A timed-out process is killed and reaped and returns 504.
Before importing the parser, the child applies Linux `RLIMIT_AS` to cap its
address space (256 MiB by default). This is not a reserved amount of physical
memory or a container RSS limit. Four loading slots can run up to four such
processes per application worker, in addition to the parent application's memory.

Both the child and parent cap the final JSON or HTML response at 20 MiB by
default. Responses are buffered until the process succeeds; partial, oversized,
invalid, or memory-exhausted results return a generic 400 without truncation.
Failure to start the worker returns a generic 500. Resource limits are not a
sandbox against code execution. Linux support is required; there is no fallback
which silently omits the memory limit.

## Docker

You can also run the application using Docker:

1. Build the Docker image:
    ```sh
    docker build -t calvie .
    ```

2. Make configuration readable by the application group without giving access
   to other users (keep its existing administrator owner):
    ```sh
    sudo chgrp 10001 config.ini
    chmod 0640 config.ini
    ```

3. Run the Docker container with your calendar configuration:
    ```sh
    docker run --rm -p 8080:8080 -v "$PWD/config.ini:/app/config.ini:ro" calvie
    ```

The image runs as UID/GID 10001:10001. Application files remain owned by root.
TCP startup ignores forwarded headers by default. For a trusted TCP proxy,
explicitly set its actual source IPs, for example
`-e FORWARDED_ALLOW_IPS=172.20.0.10`; prevent clients from bypassing that proxy.
The proxy must overwrite or correctly sanitize incoming forwarded headers.

### Compose and host reverse proxy migration

Compose keeps `main.sock` and the optional `backup.sock` for a host proxy running
as `www-data` (UID/GID 33). Both containers use a read-only root filesystem,
drop all capabilities, prevent privilege escalation, and use an init process.
Only the socket directory and a 64 MiB `/tmp` tmpfs are writable; configuration
is mounted read-only. Missing bind sources cause startup to fail instead of
being created by Docker.
Use a current stable Docker Compose release. Old prereleases such as
`v2.0.0-rc.3` can silently discard `group_add` and bind-source creation settings;
check that `docker compose --profile upgrade config` preserves them before deploying.

Before upgrading, stop both services and prepare permissions. Run these commands
from the repository root on the deployment host, not inside the container:

```sh
docker compose --profile upgrade down
export CALVIE_UID=10001 CALVIE_GID=10001 CALVIE_SOCKET_GID=33
sudo install -d -o "$CALVIE_UID" -g "$CALVIE_SOCKET_GID" -m 2750 ./sockets
sudo rm -f ./sockets/main.sock ./sockets/backup.sock
sudo chgrp "$CALVIE_GID" config.ini
chmod 0640 config.ini
docker compose up -d main
```

Use your chosen numeric IDs in the exports for custom identities, and supply
the same variables on subsequent Compose invocations (or keep them in a local
Compose environment file). The primary application group reads `config.ini`;
the separate supplemental socket group gives the proxy access without granting
it access to configuration. Keep configuration owned by its administrator.
Ensure the proxy can traverse the socket directory's parent directories; do not
make a private home directory broadly accessible as a shortcut.

The setgid socket directory (2750) makes new sockets inherit the proxy group.
Uvicorn creates sockets with mode 0666, so access control depends on this
directory: unrelated users cannot enter it, and the proxy cannot create or delete
files there. Compose explicitly trusts forwarded headers only on these UDS-only
listeners, which have no published TCP ports. Every account allowed into the
directory must be trusted to supply those headers. The proxy must sanitize them.
Start the second service with `docker compose --profile upgrade up -d backup`.

## Testing

The project includes comprehensive unit tests. To run the tests:

```sh
uv run --locked pytest
```

Or with verbose output:
```sh
uv run --locked pytest -v
```

Dependencies are locked in `uv.lock`. After changing dependencies or the application version, run `uv lock`
and commit both `pyproject.toml` and `uv.lock`. To update locked versions within
the declared ranges, run `uv lock --upgrade` and rerun the tests.

## License

This project is licensed under the MIT License.
