from uuid import UUID


class GatewayError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        status: int,
        *,
        retryable: bool = False,
        original_request_id: UUID | None = None,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.retryable = retryable
        self.original_request_id = original_request_id
        self.retry_after_seconds = retry_after_seconds
