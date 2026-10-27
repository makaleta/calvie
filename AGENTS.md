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
- Download and parsing failures return generic HTTP 400 responses without source URLs. Connect/read timeouts are 5/10 seconds; download size, recurrence expansion, and event-loop isolation still need separate resource controls.
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

Add focused regression tests for changed route behavior, timezone handling, locale formatting, `.ics` URL validation, and theme selection. For automatic themes, check that dark styles remain inside the preference media query and that explicit themes preserve legacy parameter compatibility.

After changing the application version in `pyproject.toml`, run `uv lock` and commit the updated `uv.lock` too. CI uses `--locked` and rejects stale project metadata even when dependency versions are unchanged.
