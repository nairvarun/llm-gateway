import argparse
import asyncio
import stat
from pathlib import Path

import pytest
from sqlalchemy import func, select, text

from app.cli import run, wait_database, write_private_key
from app.domain.models import StateUnavailable
from app.persistence.database import database_engine
from app.persistence.models import Credential
from app.persistence.store import PostgresStore


def test_private_key_creation_never_overwrites(tmp_path: Path) -> None:
    target = tmp_path / "credentials" / "key"
    write_private_key(target, "synthetic-test-only")
    assert target.read_text() == "synthetic-test-only\n"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    with pytest.raises(FileExistsError):
        write_private_key(target, "replacement")


@pytest.mark.integration
async def test_local_bootstrap_is_repeatable_and_does_not_print_key(
    postgres: PostgresStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async with postgres.engine.connect() as connection:
        schema = await connection.scalar(text("SELECT current_schema()"))
    monkeypatch.setenv("GATEWAY_DATABASE_SCHEMA", str(schema))
    monkeypatch.setenv(
        "GATEWAY_DATABASE_URL", str(postgres.engine.url.render_as_string(hide_password=False))
    )
    target = tmp_path / "client-key"
    args = argparse.Namespace(command="seed-local", key_file=str(target), operator=False)
    await run(args)
    key = (await asyncio.to_thread(target.read_text)).strip()
    await run(args)
    assert key not in capsys.readouterr().out
    async with postgres.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Credential)) == 1


async def test_startup_wait_is_bounded() -> None:
    engine = database_engine(
        "postgresql+asyncpg://gateway:local-development-only@127.0.0.1:1/gateway"
    )
    try:
        with pytest.raises(StateUnavailable):
            await wait_database(PostgresStore(engine), 0.05)
    finally:
        await engine.dispose()
