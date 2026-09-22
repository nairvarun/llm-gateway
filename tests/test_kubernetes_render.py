import json
from pathlib import Path

import pytest

from deploy.kubernetes.render import render_manifests


def outputs() -> dict[str, object]:
    return {
        "region": {"value": "ap-south-1"},
        "vpc_cidr": {"value": "10.42.0.0/16"},
        "kubernetes_namespace": {"value": "llm-gateway"},
        "runtime_image_repository": {
            "value": "365712037872.dkr.ecr.ap-south-1.amazonaws.com/llm-gateway-staging"
        },
        "runtime_secret_names": {
            "value": {
                "database-url": "llm-gateway-staging/database-url",
                "replay-key": "llm-gateway-staging/replay-key",
                "cache-key": "llm-gateway-staging/cache-key",
                "input-hash-key": "llm-gateway-staging/input-hash-key",
                "smoke-client-key": "llm-gateway-staging/smoke-client-key",
                "tls-cert": "llm-gateway-staging/tls-cert",
                "tls-key": "llm-gateway-staging/tls-key",
            }
        },
        "runtime_config": {
            "value": {
                "GATEWAY_PROVIDER": "mock",
                "GATEWAY_REDIS_URL": "rediss://cache.internal:6379",
                "GATEWAY_CACHE_REDIS_URL": "rediss://cache.internal:6379",
                "GATEWAY_REPLAY_RETENTION_HOURS": "24",
                "GATEWAY_CACHE_TTL_SECONDS": "3600",
                "GATEWAY_METADATA_RETENTION_DAYS": "30",
            }
        },
    }


def test_render_manifests_uses_digest_and_contains_no_secret_values(tmp_path: Path) -> None:
    digest = f"sha256:{'a' * 64}"
    paths = render_manifests(outputs(), digest, tmp_path)
    rendered = "\n".join(path.read_text(encoding="utf-8") for path in paths)

    assert len(paths) == 11
    assert f"llm-gateway-staging@{digest}" in rendered
    assert "__" not in rendered
    assert "GATEWAY_PROVIDER: mock" in rendered
    assert "/mnt/secrets-store/database-url" in rendered
    assert "postgresql+asyncpg://" not in rendered


def test_render_manifests_refuses_live_provider(tmp_path: Path) -> None:
    terraform_outputs = outputs()
    runtime = terraform_outputs["runtime_config"]
    assert isinstance(runtime, dict)
    value = runtime["value"]
    assert isinstance(value, dict)
    value["GATEWAY_PROVIDER"] = "live"

    with pytest.raises(ValueError, match="refuses to enable a live provider"):
        render_manifests(terraform_outputs, f"sha256:{'b' * 64}", tmp_path)


def test_renderer_cli_fixture_is_json_serializable() -> None:
    assert json.loads(json.dumps(outputs())) == outputs()
