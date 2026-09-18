# AGENTS.md

## Purpose

This repository contains Calvie, a small FastAPI application that fetches `.ics` calendars and renders them either as JSON event data or as a minimal embeddable HTML iframe.

## Stack

- Python 3.13+ (below 4.0); CI and Docker use Python 3.13
- Poetry for dependency management
- FastAPI + Starlette
- Jinja2 templates
- Babel for locale-aware formatting
- icalevents for calendar parsing
- pytz for timezone handling

## Repository layout

- `main.py`: application entrypoint and all route logic
- `templates/iframe.html`: iframe event rendering
- `templates/error.html`: error rendering
- `pyproject.toml`: project metadata and dependencies
- `poetry.lock`: locked dependency versions
- `tests/test_main.py`: route, URL validation, configuration, and localization tests
- `tests/test_color_scheme.py`: iframe theme regression tests
- `.github/workflows/`: test, Docker publishing, and CodeQL workflows
- `README.md`: setup and usage notes

## Local setup

Run commands from the repository root; configuration and template paths are relative to the working directory.

1. Install dependencies, including the development test tools:

   `poetry install`

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

   `poetry run uvicorn main:app --reload`

## Runtime behavior

- `GET /` returns HTTP 418 with a teapot response.
- `GET /cal/{name:path}` returns parsed iCal events as JSON.
- `GET /iframe/{name:path}` renders a localized HTML view of events.
- Calendar sources come from `config.ini`, unless the path itself is a valid `.ics` URL.
- Configuration is loaded at import time; restart the app after changing `config.ini`. Built-in defaults are timezone `UTC`, 40 future days, locale `en_GB`, and width 300.
- Both calendar routes accept `timezone` and `days`; the iframe also accepts `locale`, `width`, `colour`, and `color_scheme`.
- `color_scheme` accepts `light`, `dark`, `normal`, `light dark`, and `dark light`. The two mixed values follow the browser preference; `normal` keeps the default light styling. Without a theme parameter, the iframe follows the browser preference.
- Legacy `colour=white|black` remains supported. When both theme parameters are supplied, `color_scheme` takes precedence.

## Change guidance

- Keep the app lightweight; avoid introducing structure that is disproportionate to the repository size.
- Preserve backward compatibility for the existing routes unless explicitly asked to change them.
- Treat `config.ini` as user-provided local configuration; do not commit secrets or environment-specific values.
- Template changes should preserve the minimal embeddable iframe use case.
- If changing dependencies, update both `pyproject.toml` and `poetry.lock` and keep additions justified by actual app needs.

## Validation

For code changes, use the smallest relevant validation available:

- Run focused tests with `poetry run pytest tests/test_main.py` or `poetry run pytest tests/test_color_scheme.py`; run the full suite with `poetry run pytest` before merging.
- Use `poetry run uvicorn main:app --reload` for startup verification when runtime changes warrant it, with targeted manual checks of `/cal/...` and `/iframe/...`.
- Unit tests mock calendar fetching and do not need external calendar access. Avoid creating or modifying local `config.ini` for tests: the default configuration test expects the built-in defaults.

Add focused regression tests for changed route behavior, timezone handling, locale formatting, `.ics` URL validation, and theme selection. For automatic themes, check that dark styles remain inside the preference media query and that explicit themes preserve legacy parameter compatibility.
