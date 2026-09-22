import os
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    ("migration_setting", "expected_commands", "expected_returncode"),
    [
        (None, ["gateway", "alembic", "uvicorn"], 0),
        ("false", ["gateway", "uvicorn"], 0),
        ("invalid", ["gateway"], 1),
    ],
)
def test_startup_migration_control(
    tmp_path: Path,
    migration_setting: str | None,
    expected_commands: list[str],
    expected_returncode: int,
) -> None:
    trace = tmp_path / "commands.txt"
    for command in ("gateway", "alembic", "uvicorn"):
        executable = tmp_path / command
        executable.write_text(f"#!/bin/sh\nprintf '{command}\\n' >> \"$STARTUP_TRACE\"\n")
        executable.chmod(0o755)

    environment = os.environ.copy()
    environment["PATH"] = f"{tmp_path}:{environment['PATH']}"
    environment["STARTUP_TRACE"] = str(trace)
    environment.pop("GATEWAY_RUN_MIGRATIONS", None)
    if migration_setting is not None:
        environment["GATEWAY_RUN_MIGRATIONS"] = migration_setting

    result = subprocess.run(
        ["sh", "deploy/start.sh"],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == expected_returncode
    assert trace.read_text().splitlines() == expected_commands


def test_startup_requires_complete_tls_file_pair(tmp_path: Path) -> None:
    trace = tmp_path / "commands.txt"
    for command in ("gateway", "uvicorn"):
        executable = tmp_path / command
        executable.write_text(f"#!/bin/sh\nprintf '{command}\\n' >> \"$STARTUP_TRACE\"\n")
        executable.chmod(0o755)

    environment = os.environ.copy()
    environment["PATH"] = f"{tmp_path}:{environment['PATH']}"
    environment["STARTUP_TRACE"] = str(trace)
    environment["GATEWAY_RUN_MIGRATIONS"] = "false"
    environment["GATEWAY_TLS_CERT_FILE"] = "/mounted/tls-cert"
    environment.pop("GATEWAY_TLS_KEY_FILE", None)

    result = subprocess.run(
        ["sh", "deploy/start.sh"],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "Both GATEWAY_TLS_CERT_FILE" in result.stderr
    assert trace.read_text().splitlines() == ["gateway"]
