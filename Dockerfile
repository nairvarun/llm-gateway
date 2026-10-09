# Multi-stage build: dependencies are resolved in the first stage, the runtime image only
# carries the virtualenv and the source, and runs as a non-root user.
FROM python:3.13-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY gateway ./gateway

FROM python:3.13-slim
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin gateway
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY --from=build /app/gateway ./gateway
ENV PATH=/app/.venv/bin:$PATH PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 10001
EXPOSE 8080
# --timeout-graceful-shutdown must equal shutdown.drain_s in the config (330 by default).
CMD ["uvicorn", "gateway.app:create_app_from_env", "--factory", "--host", "0.0.0.0", \
     "--port", "8080", "--timeout-graceful-shutdown", "330", "--no-access-log"]
