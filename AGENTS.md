# AGENTS.md

## Purpose

This repository contains Calvie, a small FastAPI application that fetches `.ics` calendars and renders them either as JSON event data or as a minimal embeddable HTML iframe.

## Stack

- Python 3.14+ (below 4.0); CI and Docker use Python 3.14
- uv for dependency management
- FastAPI + Starlette
- Jinja2 templates
- Babel for locale-aware formatting
- icalevents for calendar parsing
- pytz for timezone handling

## Repository layout

- `main.py`: application entrypoint and all route logic
- `calendar_source.py`: URL validation, DNS/IP policy, and pinned HTTP(S) downloads
- `calendar_processing.py`: subprocess supervision, cancellation, bounded IPC, and cleanup
- `calendar_worker.py`: one-shot memory-limited parser and JSON/HTML response generation
- `templates/iframe.html`: iframe event rendering
- `templates/error.html`: error rendering
- `pyproject.toml`: project metadata and dependencies
- `uv.lock`: locked dependency versions
- `tests/test_main.py`: route, URL validation, configuration, and localization tests
- `tests/test_calendar_integration.py`: real parser, JSON serialization, and HTML regression tests
- `tests/test_color_scheme.py`: iframe theme regression tests
- `.github/workflows/`: test, Docker publishing, and CodeQL workflows
- `README.md`: setup and usage notes

## Local setup

Run commands from the repository root; configuration and template paths are relative to the working directory.

1. Install dependencies, including the development test tools:

   `uv sync --locked`

2. For named calendars, create `config.ini` in the repository root. Direct calendar URLs work without this file. Example:

   ```ini
   [DEFAULT]
   timezone = Europe/London
   days to future = 40
   locale = en_GB
   width = 355

   [exampleCal]
   url = https://example.com/calendar.ics
   ```

3. Run the app:

   `uv run --locked uvicorn main:app --reload`

## Runtime behavior

- `GET /` returns HTTP 418 with a teapot response.
- `GET /cal/{name:path}` returns parsed iCal events as JSON.
- `GET /iframe/{name:path}` renders a localized HTML view of events.
- Calendar sources come from `config.ini`, unless the path itself is a valid `.ics` URL.
- All sources require public destination IPs by default. A named calendar may explicitly set `allow private network = true`; it is forbidden in `DEFAULT` and cannot be supplied through HTTP parameters. Its exception applies only to the original scheme, hostname, and port.
- Downloads pin the validated destination IP while preserving HTTP Host, TLS SNI, and certificate verification. Every redirect is checked again; at most five redirects are allowed, and HTTPS-to-HTTP redirects are rejected. Environment proxies and automatic retries are not used.
- Direct URLs retain nonstandard ports and schemeless HTTP forms. Reject URL credentials, fragments, and ambiguous forms; validate `.ics` against the actual path, with the legacy bare-host exception. Configured URLs need no `.ics` suffix.
- Downloads run in AnyIO threads; parsing, timezone conversion, and JSON/HTML generation run in a fresh Linux subprocess. Both routes share four slots per worker, with immediate HTTP 503 and `Retry-After: 1` on saturation. Cancelled downloads retain their slot until the thread finishes and skip processing; cancelled children are killed and reaped before releasing capacity.
- Global resource limits are read only from `DEFAULT` at import time: `max calendar bytes` 5242880 (both wire and decompressed), `max days` 366, `max events` 5000, `download timeout` 60 seconds, and `max concurrent calendars` 4. All must be positive integers. Per-calendar and HTTP overrides are ignored.
- `days` and configured `days to future` must be within 1..`max days` (HTTP 422 otherwise). Download/parser/size/event-count failures return generic HTTP 400 without source URLs; timeouts return 504. Iframe errors preserve status and headers.
- Downloads read at most 64 KiB per block and use bounded incremental gzip/deflate decoding. A shared monotonic budget covers all hops, with connect/read timeouts capped at 5/10 seconds. It is cooperative, not a hard deadline for DNS or blocked operations.
- Processing limits, also read only from `DEFAULT`, are `parser timeout` 10 seconds, `parser memory mib` 256, and `max response bytes` 20971520. The child sets Linux `RLIMIT_AS` before third-party imports. Timeout covers startup through response preparation and triggers kill/reap (504). Memory, parsing, event-count, and response-size failures return 400; worker startup failures return 500. These are resource limits, not a code-execution sandbox or an RSS limit.
- Linux with `RLIMIT_AS` is mandatory at startup. IPC uses JSON input and bounded response bytes, never pickle; the child receives calendar content and validated options, not source URLs or configuration. Send responses only after successful child completion; discard partial output on failure. Keep SSRF-protected downloads in the parent.
- Validate timezone and locale before downloading (422). Event count is checked after parsing and before timezone conversion; all parser/renderer allocations and expansion occur within the child's time and address-space limits. Both child and parent enforce the response byte limit.
- Configuration is loaded at import time; restart the app after changing `config.ini`. Built-in defaults are timezone `UTC`, 40 future days, locale `en_GB`, and width 300.
- Both calendar routes accept `timezone` and `days`; the iframe also accepts `locale`, `width`, `colour`, and `color_scheme`.
- `color_scheme` accepts `light`, `dark`, `normal`, `light dark`, and `dark light`. The two mixed values follow the browser preference; `normal` keeps the default light styling. Without a theme parameter, the iframe follows the browser preference.
- Legacy `colour=white|black` remains supported. When both theme parameters are supplied, `color_scheme` takes precedence.

## Change guidance

- Keep the app lightweight; avoid introducing structure that is disproportionate to the repository size.
- Preserve backward compatibility for the existing routes unless explicitly asked to change them.
- Treat `config.ini` as user-provided local configuration; do not commit secrets or environment-specific values.
- Template changes should preserve the minimal embeddable iframe use case.
- If changing dependencies, update both `pyproject.toml` and `uv.lock` and keep additions justified by actual app needs.

## Validation

For code changes, use the smallest relevant validation available:

- Run focused tests with `uv run --locked pytest tests/test_main.py` or `uv run --locked pytest tests/test_color_scheme.py`; run the full suite with `uv run --locked pytest` before merging.
- Use `uv run --locked uvicorn main:app --reload` for startup verification when runtime changes warrant it, with targeted manual checks of `/cal/...` and `/iframe/...`.
- Tests isolate configuration from local `config.ini` and mock calendar downloading; integration tests use the real parser with fixed calendar data and time. No external calendar access is needed.
- `tests/test_calendar_source.py` covers the downloader with deterministic DNS/HTTP fixtures and local TLS. Preserve tests for DNS rebinding, mixed DNS answers, redirects, origin-scoped private exceptions, HTTPS identity, and error redaction when changing downloads.
- `tests/test_resource_limits.py` covers worker admission/cancellation, global configuration, day/event bounds, streaming/decompression budgets, and local chunked/gzip responses. Keep concurrency tests deterministic with synchronization events, not timing sleeps.
- `tests/test_calendar_processing.py` exercises real subprocess memory/time/output limits, kill/reap, startup and partial-output errors, cancellation/disconnects, and full JSON compatibility. Pass start/end times explicitly; monkeypatches do not propagate to child processes. Keep actual parser tests in addition to mocked route boundaries.

Add focused regression tests for changed route behavior, timezone handling, locale formatting, `.ics` URL validation, and theme selection. For automatic themes, check that dark styles remain inside the preference media query and that explicit themes preserve legacy parameter compatibility.

After changing the application version in `pyproject.toml`, run `uv lock` and commit the updated `uv.lock` too. CI uses `--locked` and rejects stale project metadata even when dependency versions are unchanged.
