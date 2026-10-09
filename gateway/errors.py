"""Gateway errors, rendered in the OpenAI error shape so existing SDKs behave correctly."""

from __future__ import annotations


class GatewayError(Exception):
    status: int = 500
    type: str = "api_error"

    def __init__(
        self, message: str, *, code: str | None = None, retry_after: int | None = None
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.retry_after = retry_after

    def body(self) -> dict:
        return {"error": {"message": self.message, "type": self.type, "code": self.code}}

    def headers(self) -> dict[str, str]:
        return {"Retry-After": str(self.retry_after)} if self.retry_after is not None else {}


class BadRequest(GatewayError):
    status, type = 400, "invalid_request_error"


class Unauthorized(GatewayError):
    status, type = 401, "authentication_error"


class BudgetExceeded(GatewayError):
    status, type = 402, "insufficient_quota"


class Forbidden(GatewayError):
    status, type = 403, "permission_error"


class NotFound(GatewayError):
    status, type = 404, "invalid_request_error"


class RateLimited(GatewayError):
    status, type = 429, "rate_limit_error"


class UpstreamFailed(GatewayError):
    status, type = 502, "api_error"


class Unavailable(GatewayError):
    status, type = 503, "api_error"


class UpstreamTimeout(GatewayError):
    status, type = 504, "timeout_error"
