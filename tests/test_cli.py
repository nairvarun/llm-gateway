import argparse
import asyncio
import json
import stat
from pathlib import Path

import pytest
from sqlalchemy import func, select, text

from app.cli import run, wait_database, write_private_key
from app.domain.errors import GatewayError
from app.domain.models import StateUnavailable
from app.persistence.bootstrap import bootstrap_local
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


@pytest.mark.integration
async def test_staging_smoke_bootstrap_registers_external_key_idempotently(
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
    external_key = "gw_" + "staging-smoke-synthetic-key-0123456789"
    key_file = tmp_path / "staging-smoke-key"
    key_file.write_text(external_key, encoding="utf-8")
    args = argparse.Namespace(command="bootstrap-staging-smoke", key_file=str(key_file))

    await run(args)
    await run(args)

    assert external_key not in capsys.readouterr().out
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


@pytest.mark.integration
async def test_operator_routing_cli_commands(
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
    tenant_key, tenant = await bootstrap_local(postgres)
    operator_key, _ = await bootstrap_local(postgres, role="operator")
    tenant_file, operator_file = tmp_path / "tenant", tmp_path / "operator"
    write_private_key(tenant_file, tenant_key)
    write_private_key(operator_file, operator_key)
    policy = (await postgres.registry()).policy.model_dump(mode="json")
    policy_file = tmp_path / "policy.json"
    policy_file.write_text(json.dumps(policy))
    await run(
        argparse.Namespace(
            command="publish-config",
            key_file=str(operator_file),
            kind="policy",
            name="mock-policy",
            version="cli-v1",
            payload_file=str(policy_file),
        )
    )
    await run(
        argparse.Namespace(
            command="activate-policy",
            key_file=str(operator_file),
            name="mock-policy",
            version="cli-v1",
        )
    )
    assert (await postgres.registry()).policy_version == "cli-v1"
    await run(
        argparse.Namespace(
            command="disable-provider",
            key_file=str(operator_file),
            provider="mock",
        )
    )
    assert "mock" in (await postgres.registry()).disabled_providers
    await run(
        argparse.Namespace(
            command="enable-provider",
            key_file=str(operator_file),
            provider="mock",
        )
    )
    await run(
        argparse.Namespace(
            command="rollback-policy",
            key_file=str(operator_file),
            name="mock-policy",
            version="v2",
        )
    )
    assert (await postgres.registry()).policy_version == "v2"
    assert tenant.role == "tenant"
    assert operator_key not in capsys.readouterr().out


@pytest.mark.integration
async def test_operator_cache_cli_is_audited_and_tenant_key_cannot_invalidate(
    postgres: PostgresStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with postgres.engine.connect() as connection:
        schema = await connection.scalar(text("SELECT current_schema()"))
    monkeypatch.setenv("GATEWAY_DATABASE_SCHEMA", str(schema))
    monkeypatch.setenv(
        "GATEWAY_DATABASE_URL", str(postgres.engine.url.render_as_string(hide_password=False))
    )
    _, tenant = await bootstrap_local(postgres)
    operator_key, _ = await bootstrap_local(postgres, role="operator")
    operator_file = tmp_path / "operator"
    write_private_key(operator_file, operator_key)
    identity = "a" * 64
    assert await postgres.cache_generations(tenant, identity) == (1, 1)
    with pytest.raises(GatewayError):
        await postgres.invalidate_cache_exact(
            tenant, tenant.tenant_id, tenant.application_id, identity
        )
    for command in ("revoke-cache", "approve-cache"):
        await run(
            argparse.Namespace(
                command=command, key_file=str(operator_file), tenant_id=str(tenant.tenant_id)
            )
        )
    await run(
        argparse.Namespace(
            command="invalidate-cache-exact",
            key_file=str(operator_file),
            tenant_id=str(tenant.tenant_id),
            application_id=tenant.application_id,
            key_hash=identity,
        )
    )
    await run(
        argparse.Namespace(
            command="invalidate-cache-namespace",
            key_file=str(operator_file),
            tenant_id=str(tenant.tenant_id),
            application_id=tenant.application_id,
        )
    )
    assert await postgres.cache_generations(tenant, identity) == (4, 2)
