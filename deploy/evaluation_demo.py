"""Credential-free-provider, synthetic local evaluation lifecycle demo."""

import argparse
import asyncio
import json
import os
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID

import httpx

DEMO_RUN = {
    "dataset_name": "synthetic-gateway",
    "dataset_version": "v1",
    "model_ids": ["mock-text-v1@v2"],
    "policy_version": "mock-policy@v2",
    "threshold_profile": "synthetic-contract@v1",
}


async def run(args: argparse.Namespace) -> None:
    base = os.environ.get("GATEWAY_SMOKE_URL", "http://127.0.0.1:8000").rstrip("/")
    parsed = urlparse(base)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost"}
        or parsed.username
        or parsed.password
        or parsed.path
        or parsed.query
    ):
        raise ValueError("Evaluation demo requires a local loopback API URL")
    key = (await asyncio.to_thread(Path(args.key_file).read_text)).strip()
    async with httpx.AsyncClient(base_url=base, headers={"X-API-Key": key}, timeout=15) as client:
        if args.action == "enqueue":
            response = await client.post("/v1/evaluations/runs", json=DEMO_RUN)
            response.raise_for_status()
            body = response.json()
            if response.status_code != 202 or body["status"] != "queued":
                raise RuntimeError("Evaluation was not durably queued")
            print(json.dumps({"run_id": body["run_id"], "status": body["status"]}))
            return
        run_id = UUID(args.run_id)
        response = await client.get(f"/v1/evaluations/runs/{run_id}")
        response.raise_for_status()
        gate = await client.get(f"/v1/evaluations/runs/{run_id}/gate")
        gate.raise_for_status()
        body, decision = response.json(), gate.json()
        if body["status"] != "completed" or decision["status"] != "blocked":
            raise RuntimeError("Synthetic evaluation must complete with promotion blocked")
        if decision["reasons"] != ["missing_approved_baseline"]:
            raise RuntimeError("The expected missing-baseline gate evidence is absent")
        print(
            json.dumps(
                {
                    "run_id": str(run_id),
                    "status": body["status"],
                    "cases": len(body["cases"]),
                    "gate": decision["status"],
                    "reasons": decision["reasons"],
                },
                sort_keys=True,
            )
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline synthetic evaluation demo")
    commands = parser.add_subparsers(dest="action", required=True)
    enqueue = commands.add_parser("enqueue")
    enqueue.add_argument("--key-file", default=".local/client-key")
    verify = commands.add_parser("verify")
    verify.add_argument("run_id")
    verify.add_argument("--key-file", default=".local/client-key")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
