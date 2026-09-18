FROM python:3.14-alpine AS python
ENV PYTHONUNBUFFERED=1 \
    PYTHONFAULTHANDLER=1
WORKDIR /app

FROM python AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.17 /uv /usr/local/bin/uv
ENV UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev

FROM python AS runtime
ARG APP_VERSION=DEVEL
ENV VERSION=${APP_VERSION} \
    PATH="/app/.venv/bin:$PATH"
COPY --from=builder /app/.venv /app/.venv
COPY templates ./templates
COPY main.py ./
EXPOSE 8080
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips=*"]
