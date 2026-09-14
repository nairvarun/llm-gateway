import asyncio
import os
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from app.config import Settings
from app.persistence.database import database_engine
from app.persistence.store import PostgresStore


@pytest.fixture
async def postgres() -> AsyncIterator[PostgresStore]:
    # Each integration test migrates an isolated, randomly named schema. Never
    # truncate/drop the developer's public tables or a preexisting schema.
    url = os.environ.get("TEST_DATABASE_URL", Settings().database_url.get_secret_value())
    schema = "test_" + uuid4().hex
    admin = database_engine(url)
    try:
        async with admin.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    except Exception:
        await admin.dispose()
        pytest.fail(
            "PostgreSQL integration dependency unavailable; start the documented local database.",
            pytrace=False,
        )
    config = Config("alembic.ini")
    config.set_main_option("database_url", url.replace("%", "%%"))
    config.set_main_option("database_schema", schema)
    engine = database_engine(url, schema)
    try:
        await asyncio.to_thread(command.upgrade, config, "head")
        yield PostgresStore(engine)
    finally:
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()
