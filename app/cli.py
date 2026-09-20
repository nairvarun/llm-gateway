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
from app.persistence.bootstrap import bootstrap_local, ensure_local_configuration
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
                await ensure_local_configuration(store)
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
        elif args.command == "publish-config":
            principal = await authenticate_file(store, key_file)
            payload_file = Path(args.payload_file)
            if (await asyncio.to_thread(payload_file.stat)).st_size > 65_536:
                raise ValueError("Configuration payload exceeds 64 KiB")
            payload = json.loads(await asyncio.to_thread(payload_file.read_text))
            if not isinstance(payload, dict):
                raise ValueError("Configuration payload must be a JSON object")
            identity = await store.publish_configuration(
                principal, args.kind, args.name, args.version, payload
            )
            print(f"Validated immutable {args.kind} version published: {identity}.")
        elif args.command in {"activate-policy", "rollback-policy"}:
            principal = await authenticate_file(store, key_file)
            await store.activate_policy(
                principal, args.name, args.version, rollback=args.command == "rollback-policy"
            )
            print(f"Policy {args.name}@{args.version} activated; audit recorded.")
        elif args.command in {"disable-provider", "enable-provider"}:
            principal = await authenticate_file(store, key_file)
            await store.set_provider_disabled(
                principal, args.provider, args.command == "disable-provider"
            )
            print(f"Provider {args.provider} control updated; audit recorded.")
        elif args.command == "recover-unknown-attempt":
            principal = await authenticate_file(store, key_file)
            changed = await store.conservative_recover_attempt(principal, UUID(args.attempt_id))
            print("Conservative charge recorded." if changed else "Attempt was already reconciled.")
        elif args.command in {"approve-cache", "revoke-cache"}:
            principal = await authenticate_file(store, key_file)
            await store.set_cache_approval(
                principal, UUID(args.tenant_id), args.command == "approve-cache"
            )
            print("Cache approval updated; audit recorded.")
        elif args.command == "invalidate-cache-namespace":
            principal = await authenticate_file(store, key_file)
            generation = await store.invalidate_cache_namespace(
                principal, UUID(args.tenant_id), args.application_id
            )
            print(f"Namespace invalidated at generation {generation}; audit recorded.")
        elif args.command == "invalidate-cache-exact":
            principal = await authenticate_file(store, key_file)
            generation = await store.invalidate_cache_exact(
                principal, UUID(args.tenant_id), args.application_id, args.key_hash
            )
            print(f"Exact key invalidated at generation {generation}; audit recorded.")
        elif args.command == "spend-summary":
            principal = await authenticate_file(store, key_file)
            target = UUID(args.tenant_id) if args.tenant_id is not None else principal.tenant_id
            summary = await store.spend_summary(principal, target)
            print(json.dumps(asdict(summary), default=str, indent=2))
        elif args.command == "purge-retention":
            principal = await authenticate_file(store, key_file)
            counts = await store.purge_retention(principal, settings.metadata_retention_days)
            print(json.dumps(counts, sort_keys=True))
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
    publish = commands.add_parser("publish-config", help="Publish an immutable routing version")
    publish.add_argument("kind", choices=["policy", "model", "pricing"])
    publish.add_argument("name")
    publish.add_argument("version")
    publish.add_argument("payload_file")
    publish.add_argument("--key-file", default=".local/operator-key")
    for action in ("activate-policy", "rollback-policy"):
        command = commands.add_parser(action, help="Activate an audited policy version")
        command.add_argument("name")
        command.add_argument("version")
        command.add_argument("--key-file", default=".local/operator-key")
    for action in ("disable-provider", "enable-provider"):
        command = commands.add_parser(action, help="Change audited provider availability")
        command.add_argument("provider")
        command.add_argument("--key-file", default=".local/operator-key")
    recovery = commands.add_parser(
        "recover-unknown-attempt", help="Audit a conservative charge for unknown usage"
    )
    recovery.add_argument("attempt_id")
    recovery.add_argument("--key-file", default=".local/operator-key")
    for action in ("approve-cache", "revoke-cache"):
        command = commands.add_parser(action, help="Set audited tenant cache eligibility")
        command.add_argument("tenant_id")
        command.add_argument("--key-file", default=".local/operator-key")
    namespace = commands.add_parser(
        "invalidate-cache-namespace", help="Advance audited application cache namespace"
    )
    namespace.add_argument("tenant_id")
    namespace.add_argument("application_id")
    namespace.add_argument("--key-file", default=".local/operator-key")
    exact = commands.add_parser(
        "invalidate-cache-exact", help="Invalidate one exact cache identity"
    )
    exact.add_argument("tenant_id")
    exact.add_argument("application_id")
    exact.add_argument("key_hash")
    exact.add_argument("--key-file", default=".local/operator-key")
    spend = commands.add_parser("spend-summary", help="Read current UTC tenant allowances")
    spend.add_argument("--tenant-id")
    spend.add_argument("--key-file", default=".local/client-key")
    purge = commands.add_parser(
        "purge-retention", help="Delete expired protected content and metadata"
    )
    purge.add_argument("--key-file", default=".local/operator-key")
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
