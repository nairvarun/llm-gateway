"""SQLite Store. One connection; aiosqlite serializes calls on its own thread."""

from __future__ import annotations

import json
import secrets
from dataclasses import astuple
from hashlib import sha256
from pathlib import Path

import aiosqlite

from gateway.store.base import UsageRow, VirtualKey, month_start, utc_ts

SCHEMA = (Path(__file__).parent / "schema.sql").read_text()


def hash_key(plaintext: str) -> str:
    return sha256(plaintext.encode()).hexdigest()


def _key(row: aiosqlite.Row) -> VirtualKey:
    allowed = row["allowed_models"]
    return VirtualKey(
        id=row["id"],
        name=row["name"],
        allowed_models=json.loads(allowed) if allowed is not None else None,
        rpm_limit=row["rpm_limit"],
        monthly_budget_usd=row["monthly_budget_usd"],
        created_at=row["created_at"],
        revoked_at=row["revoked_at"],
    )


class SqliteStore:
    def __init__(self, path: str) -> None:
        self.path = path
        self._db: aiosqlite.Connection | None = None

    @property
    def db(self) -> aiosqlite.Connection:
        assert self._db is not None, "store is not open"
        return self._db

    async def open(self) -> None:
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(SCHEMA)
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def get_key_by_hash(self, hash: str) -> VirtualKey | None:
        async with self.db.execute("SELECT * FROM api_keys WHERE hash = ?", (hash,)) as cur:
            row = await cur.fetchone()
        return _key(row) if row else None

    async def get_key(self, key_id: str) -> VirtualKey | None:
        async with self.db.execute("SELECT * FROM api_keys WHERE id = ?", (key_id,)) as cur:
            row = await cur.fetchone()
        return _key(row) if row else None

    async def create_key(
        self,
        name: str,
        allowed_models: list[str] | None,
        rpm_limit: int | None,
        monthly_budget_usd: float | None,
    ) -> tuple[VirtualKey, str]:
        plaintext = "gw-" + secrets.token_urlsafe(32)
        key = VirtualKey(
            id="key_" + secrets.token_hex(8),
            name=name,
            allowed_models=allowed_models,
            rpm_limit=rpm_limit,
            monthly_budget_usd=monthly_budget_usd,
            created_at=utc_ts(),
        )
        await self.db.execute(
            "INSERT INTO api_keys (id, hash, name, allowed_models, rpm_limit,"
            " monthly_budget_usd, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                key.id,
                hash_key(plaintext),
                name,
                json.dumps(allowed_models) if allowed_models is not None else None,
                rpm_limit,
                monthly_budget_usd,
                key.created_at,
            ),
        )
        await self.db.commit()
        return key, plaintext

    async def revoke_key(self, key_id: str) -> bool:
        cur = await self.db.execute(
            "UPDATE api_keys SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
            (utc_ts(), key_id),
        )
        await self.db.commit()
        return cur.rowcount == 1

    async def record_usage(self, row: UsageRow) -> None:
        # ON CONFLICT DO NOTHING: a write that runs twice cannot double-bill.
        await self.db.execute(
            "INSERT INTO usage (request_id, key_id, ts, model_alias, provider, model, attempts,"
            " input_tokens, output_tokens, usage_estimated, cost_usd, latency_ms, ttft_ms,"
            " status, cache_hit) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(request_id) DO NOTHING",
            astuple(row),
        )
        await self.db.commit()

    async def spend_this_month(self, key_id: str) -> float:
        async with self.db.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM usage WHERE key_id = ? AND ts >= ?",
            (key_id, utc_ts(month_start())),
        ) as cur:
            (total,) = await cur.fetchone()
        return float(total)

    async def usage_summary(self, key_id: str) -> dict:
        start = month_start()
        async with self.db.execute(
            "SELECT model_alias, COUNT(*), COALESCE(SUM(input_tokens), 0),"
            " COALESCE(SUM(output_tokens), 0), COALESCE(SUM(cost_usd), 0)"
            " FROM usage WHERE key_id = ? AND ts >= ? GROUP BY model_alias ORDER BY model_alias",
            (key_id, utc_ts(start)),
        ) as cur:
            rows = await cur.fetchall()
        by_alias = [
            {
                "alias": r[0],
                "requests": r[1],
                "input_tokens": r[2],
                "output_tokens": r[3],
                "cost_usd": round(r[4], 6),
            }
            for r in rows
        ]
        return {
            "key_id": key_id,
            "month": start.strftime("%Y-%m"),
            "requests": sum(a["requests"] for a in by_alias),
            "input_tokens": sum(a["input_tokens"] for a in by_alias),
            "output_tokens": sum(a["output_tokens"] for a in by_alias),
            "cost_usd": round(sum(a["cost_usd"] for a in by_alias), 6),
            "by_alias": by_alias,
        }

    async def ping(self) -> bool:
        async with self.db.execute("SELECT 1") as cur:
            return (await cur.fetchone())[0] == 1
