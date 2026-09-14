import hashlib
import secrets

from app.domain.errors import GatewayError
from app.domain.models import Principal


def new_api_key() -> str:
    return "gw_" + secrets.token_urlsafe(32)


def hash_api_key(key: str) -> str:
    # SHA-256 verification hashes are appropriate for random 256-bit API keys,
    # not human passwords. Key plaintext is never persisted by the repository.
    return hashlib.sha256(key.encode()).hexdigest()


def require_operator(principal: Principal) -> None:
    if principal.role != "operator":
        raise GatewayError("FORBIDDEN", "Operator permission required.", 403)
