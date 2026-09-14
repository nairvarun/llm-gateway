FROM ghcr.io/astral-sh/uv:0.12.13 AS uv
FROM python:3.12-slim

COPY --from=uv /uv /uvx /usr/local/bin/
WORKDIR /service
ENV UV_LINK_MODE=copy PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./
COPY deploy/start.sh ./deploy/start.sh
RUN uv sync --frozen --no-dev && useradd --uid 10001 --create-home gateway
ENV PATH="/service/.venv/bin:$PATH"
USER gateway
EXPOSE 8000
CMD ["sh", "deploy/start.sh"]
