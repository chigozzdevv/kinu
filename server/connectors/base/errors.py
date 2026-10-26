class ConnectorError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        retryable: bool = False,
        safe_detail: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.safe_detail = safe_detail


class AmbiguousMutationError(ConnectorError):
    def __init__(self, message: str) -> None:
        super().__init__("ambiguous-mutation", message, retryable=False)


class ConnectorAuthenticationError(ConnectorError):
    def __init__(self, message: str) -> None:
        super().__init__("connector-authentication", message, retryable=False)


class ConnectorSetupRequiredError(ConnectorError):
    def __init__(self, message: str) -> None:
        super().__init__("connector-setup-required", message, retryable=False)
