# Calvie

**Calvie** is a simple iCal viewer built with FastAPI.

## Features

- View iCal events
- Simple and easy to use interface

## Requirements

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

Calendar downloading, parsing, and timezone conversion run in worker threads.
Both calendar routes share four loading slots per application worker. Once all
slots are occupied, new calendar requests receive HTTP 503 and `Retry-After: 1`
without queuing. Disconnecting a client does not free its slot until the work
finishes. With multiple Uvicorn workers, each worker has its own slots.

The following global limits can be changed only in `[DEFAULT]` in `config.ini`:

```ini
[DEFAULT]
max calendar bytes = 5242880
max days = 366
max events = 5000
download timeout = 60
max concurrent calendars = 4
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

The download budget covers all redirects and body reads and is checked between
operations. Connection/read timeouts remain at most 5/10 seconds, shortened when
the remaining budget is smaller. The budget cannot forcibly interrupt DNS or
an internally blocked operation, so it is not a strict wall-clock deadline.

**Parser isolation is still pending.** Event counts are checked after parsing.
Malicious recurrence expansion can still consume CPU and memory, and a running
parser thread cannot be forcibly stopped. This step does not provide hard parser
time or memory limits, or a separate byte limit on serialized responses.

## Docker

You can also run the application using Docker:

1. Build the Docker image:
    ```sh
    docker build -t calvie .
    ```

2. Run the Docker container with your calendar configuration:
    ```sh
    docker run --rm -p 8080:8080 -v "$PWD/config.ini:/app/config.ini:ro" calvie
    ```

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
