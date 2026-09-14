import argparse
import asyncio
import json
import math
import os
from dataclasses import asdict
from pathlib import Path
from time import monotonic
from urllib.parse import urlparse
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app import __version__
from app.config import load_settings
from app.domain.errors import GatewayError
from app.domain.models import Principal, StateUnavailable
from app.persistence.bootstrap import bootstrap_local
from app.persistence.database import database_engine
from app.persistence.store import PostgresStore
from app.security.auth import hash_api_key


async def authenticate_file(store: PostgresStore, key_file: Path) -> Principal:
    key = (await asyncio.to_thread(key_file.read_text)).strip()
    principal = await store.authenticate(hash_api_key(key))
    if principal is None:
        raise GatewayError("UNAUTHENTICATED", "Local credential is invalid or revoked.", 401)
    return principal


def write_private_key(key_file: Path, key: str) -> None:
    key_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as output:
        output.write(key + "\n")


async def wait_database(store: PostgresStore, wait_seconds: float) -> None:
    if not math.isfinite(wait_seconds) or not 0 < wait_seconds <= 60:
        raise ValueError("Startup wait must be between 0 and 60 seconds")
    deadline = monotonic() + wait_seconds
    while True:
        try:
            async with asyncio.timeout(max(0, deadline - monotonic())):
                async with store.engine.connect() as connection:
                    await connection.execute(text("SELECT 1"))
            print("Local database connection ready.")
            return
        except (SQLAlchemyError, OSError, TimeoutError):
            if monotonic() >= deadline:
                raise StateUnavailable() from None
            await asyncio.sleep(min(0.25, deadline - monotonic()))


async def run(args: argparse.Namespace) -> None:
    settings = load_settings()
    url = settings.database_url.get_secret_value()
    engine = database_engine(url, settings.database_schema)
    store = PostgresStore(engine)
    try:
        if args.command == "wait-database":
            await wait_database(store, args.timeout)
            return
        key_file = Path(args.key_file)
        if args.command == "seed-local":
            if urlparse(url).hostname not in {"127.0.0.1", "localhost", "postgres"}:
                raise ValueError("Local bootstrap refuses a non-local database host")
            if await asyncio.to_thread(key_file.exists):
                principal = await authenticate_file(store, key_file)
                print(f"Existing local credential verified for tenant {principal.tenant_id}.")
                return
            if "sources" in (await asyncio.to_thread(key_file.resolve)).parts:
                raise ValueError("Cannot generate credentials under sources/")
            key, principal = await bootstrap_local(
                store, role="operator" if args.operator else "tenant"
            )
            await asyncio.to_thread(write_private_key, key_file, key)
            print(f"Local credential stored privately in {key_file}; tenant {principal.tenant_id}.")
        elif args.command == "inspect-request":
            principal = await authenticate_file(store, key_file)
            evidence = await store.evidence(principal, UUID(args.request_id))
            if evidence is None:
                raise GatewayError("FORBIDDEN", "Request unavailable in the authorized scope.", 403)
            print(json.dumps(asdict(evidence), default=str, indent=2))
        elif args.command == "revoke-key":
            principal = await authenticate_file(store, key_file)
            await store.revoke(principal, UUID(args.credential_id))
            print("Credential revocation recorded.")
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline LLM gateway tools")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    wait = commands.add_parser(
        "wait-database", help="Wait for bounded local startup dependency readiness"
    )
    wait.add_argument("--timeout", type=float, default=30)
    seed = commands.add_parser(
        "seed-local", help="Create/verify a synthetic local tenant and private key file"
    )
    seed.add_argument("--key-file", default=".local/client-key")
    seed.add_argument("--operator", action="store_true")
    inspect = commands.add_parser(
        "inspect-request", help="Read privacy-safe authorized request evidence"
    )
    inspect.add_argument("request_id")
    inspect.add_argument("--key-file", default=".local/client-key")
    revoke = commands.add_parser("revoke-key", help="Revoke a key using an operator credential")
    revoke.add_argument("credential_id")
    revoke.add_argument("--key-file", default=".local/operator-key")
    args = parser.parse_args()
    try:
        asyncio.run(run(args))
    except GatewayError as error:
        parser.exit(1, f"{error.code}: {error.message}\n")
    except (SQLAlchemyError, OSError, ValueError, StateUnavailable):
        parser.exit(
            1,
            "Local operation failed. Check database availability, migrations, "
            "GATEWAY_ configuration, and private key-file permissions.\n",
        )


if __name__ == "__main__":
    main()
