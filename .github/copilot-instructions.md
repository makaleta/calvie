# Calvie development

Read `AGENTS.md` for repository layout, runtime behavior, and change guidance.

- Use Python 3.14 and uv. Run commands from the repository root.
- Install dependencies with `uv sync --locked`.
- Run tests with `uv run --locked pytest`; tests do not need network calendar access or local configuration.
- Start the app with `uv run --locked uvicorn main:app --reload`.
- Named calendars use a local `config.ini`; direct calendar URLs use built-in defaults without this file. Never commit local configuration.
- Keep `pyproject.toml` and `uv.lock` synchronized when changing dependencies.
- Verify runtime changes with focused tests and run the full suite before merging.
- Docker uses Python 3.14 Alpine and production dependencies only; build with `docker build -t calvie .`.
- Preserve existing routes, localized iframe output, and legacy `colour` compatibility.
