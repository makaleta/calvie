FROM python:3.12-alpine AS python
ENV PYTHONUNBUFFERED=true \
    PYTHONFAULTHANDLER=true \
    DEBIAN_FRONTEND=noninteractive
WORKDIR /app

FROM python AS poetry
ENV POETRY_HOME=/opt/poetry \
    POETRY_VIRTUALENVS_IN_PROJECT=true
ENV PATH="$POETRY_HOME/bin:$PATH"
SHELL ["/bin/ash", "-o", "pipefail", "-c"]
# hadolint ignore=DL3018
RUN python -c 'from urllib.request import urlopen; print(urlopen("https://install.python-poetry.org").read().decode())' | python -
COPY poetry.lock pyproject.toml /app/
RUN poetry install --no-interaction --no-ansi -v

FROM python AS runtime
ARG APP_VERSION=DEVEL
ENV APP_VERSION=${APP_VERSION}
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH=$PYTHONPATH:"/app"
COPY --from=poetry /app /app

# Creating folders, and files for a project:
COPY templates ./templates
COPY main.py main.py


CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips='*'"]
