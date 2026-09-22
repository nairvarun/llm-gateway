"""Render secret-free EKS manifests from reviewed Terraform outputs."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

TEMPLATE_DIRECTORY = Path(__file__).with_name("templates")
DEFAULT_OUTPUT_DIRECTORY = Path(".local/kubernetes/staging")
IMAGE_DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")
SAFE_VALUE_PATTERN = re.compile(r"[A-Za-z0-9._:/@-]+")


def _output(outputs: dict[str, Any], name: str) -> Any:
    candidate = outputs.get(name)
    if not isinstance(candidate, dict) or "value" not in candidate:
        raise ValueError(f"Terraform output {name!r} is missing")
    return candidate["value"]


def _safe_scalar(value: Any, name: str) -> str:
    rendered = str(value)
    if not rendered or SAFE_VALUE_PATTERN.fullmatch(rendered) is None:
        raise ValueError(f"Terraform output {name!r} is not a safe manifest scalar")
    return rendered


def render_manifests(
    terraform_outputs: dict[str, Any], image_digest: str, output_directory: Path
) -> list[Path]:
    """Render reviewed, non-secret outputs into a git-ignored directory."""
    if IMAGE_DIGEST_PATTERN.fullmatch(image_digest) is None:
        raise ValueError("Image digest must be sha256 followed by 64 lowercase hex characters")

    secret_names = _output(terraform_outputs, "runtime_secret_names")
    runtime_config = _output(terraform_outputs, "runtime_config")
    if not isinstance(secret_names, dict) or not isinstance(runtime_config, dict):
        raise ValueError("Terraform runtime outputs have an invalid shape")

    required_secrets = {
        "database-url",
        "replay-key",
        "cache-key",
        "input-hash-key",
        "smoke-client-key",
        "tls-cert",
        "tls-key",
    }
    if set(secret_names) != required_secrets:
        raise ValueError("Terraform runtime_secret_names does not contain the reviewed key set")

    required_config = {
        "GATEWAY_PROVIDER",
        "GATEWAY_REDIS_URL",
        "GATEWAY_CACHE_REDIS_URL",
        "GATEWAY_REPLAY_RETENTION_HOURS",
        "GATEWAY_CACHE_TTL_SECONDS",
        "GATEWAY_METADATA_RETENTION_DAYS",
    }
    if set(runtime_config) != required_config:
        raise ValueError("Terraform runtime_config does not contain the reviewed key set")
    if runtime_config["GATEWAY_PROVIDER"] != "mock":
        raise ValueError("EKS rendering refuses to enable a live provider")

    repository = _safe_scalar(_output(terraform_outputs, "runtime_image_repository"), "repository")
    replacements = {
        "__AWS_REGION__": _safe_scalar(_output(terraform_outputs, "region"), "region"),
        "__CACHE_SECRET_NAME__": _safe_scalar(secret_names["cache-key"], "cache secret"),
        "__DATABASE_SECRET_NAME__": _safe_scalar(secret_names["database-url"], "database secret"),
        "__IMAGE_URI__": f"{repository}@{image_digest}",
        "__INPUT_HASH_SECRET_NAME__": _safe_scalar(
            secret_names["input-hash-key"], "input hash secret"
        ),
        "__KUBERNETES_NAMESPACE__": _safe_scalar(
            _output(terraform_outputs, "kubernetes_namespace"), "namespace"
        ),
        "__REDIS_URL__": _safe_scalar(runtime_config["GATEWAY_REDIS_URL"], "Redis URL"),
        "__REPLAY_SECRET_NAME__": _safe_scalar(secret_names["replay-key"], "replay secret"),
        "__SMOKE_CLIENT_SECRET_NAME__": _safe_scalar(
            secret_names["smoke-client-key"], "smoke client secret"
        ),
        "__TLS_CERT_SECRET_NAME__": _safe_scalar(secret_names["tls-cert"], "TLS certificate"),
        "__TLS_KEY_SECRET_NAME__": _safe_scalar(secret_names["tls-key"], "TLS key"),
        "__VPC_CIDR__": _safe_scalar(_output(terraform_outputs, "vpc_cidr"), "VPC CIDR"),
    }

    output_directory.mkdir(parents=True, exist_ok=True)
    rendered_paths: list[Path] = []
    for template_path in sorted(TEMPLATE_DIRECTORY.glob("*.yaml")):
        content = template_path.read_text(encoding="utf-8")
        for placeholder, value in replacements.items():
            content = content.replace(placeholder, value)
        remaining = sorted(set(re.findall(r"__[A-Z0-9_]+__", content)))
        if remaining:
            raise ValueError(f"Unresolved manifest placeholders: {', '.join(remaining)}")
        destination = output_directory / template_path.name
        destination.write_text(content, encoding="utf-8")
        rendered_paths.append(destination)
    if not rendered_paths:
        raise ValueError("No Kubernetes manifest templates were found")
    return rendered_paths


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--terraform-output", type=Path, required=True)
    parser.add_argument("--image-digest", required=True)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIRECTORY)
    args = parser.parse_args()

    raw_outputs = json.loads(args.terraform_output.read_text(encoding="utf-8"))
    if not isinstance(raw_outputs, dict):
        raise ValueError("Terraform output JSON must be an object")
    rendered = render_manifests(raw_outputs, args.image_digest, args.output_dir)
    print(f"Rendered {len(rendered)} manifests to {args.output_dir}")


if __name__ == "__main__":
    main()
