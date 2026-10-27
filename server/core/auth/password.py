from dataclasses import dataclass
from typing import Any, Literal, NoReturn
from urllib.parse import urlsplit

import httpx
from pydantic import SecretStr

from core.auth.cognito import CognitoConfig
from core.auth.forms import AttemptLimitError, FormError
from core.errors import IdentityUnavailableError


@dataclass(frozen=True, slots=True)
class SignInResult:
    next: Literal["complete", "verify", "forgot"]
    token: SecretStr | None = None


class CognitoError(Exception):
    pass


class PasswordClient:
    def __init__(self, config: CognitoConfig, http: httpx.AsyncClient) -> None:
        self.config, self.http = config, http
        self.endpoint = "https://" + str(urlsplit(config.issuer).hostname) + "/"

    async def signin(self, email: str, password: SecretStr) -> SignInResult:
        try:
            result = await self.call(
                "InitiateAuth",
                {
                    "AuthFlow": "USER_PASSWORD_AUTH",
                    "AuthParameters": {"USERNAME": email, "PASSWORD": password.get_secret_value()},
                },
            )
        except CognitoError as error:
            if str(error) == "UserNotConfirmedException":
                return SignInResult("verify")
            if str(error) == "PasswordResetRequiredException":
                return SignInResult("forgot")
            if str(error) in {"NotAuthorizedException", "UserNotFoundException"}:
                raise FormError("Email or password is incorrect.", 401) from None
            self.failure(error)
        token = result.get("AuthenticationResult", {}).get("IdToken")
        if result.get("ChallengeName"):
            raise FormError(
                "This account needs additional verification. Contact your administrator.", 403
            )
        if not isinstance(token, str) or not token:
            raise IdentityUnavailableError("Sign-in service returned an invalid response.")
        return SignInResult("complete", SecretStr(token))

    async def signup(self, email: str, name: str, password: SecretStr) -> bool:
        try:
            result = await self.call(
                "SignUp",
                {
                    "Username": email,
                    "Password": password.get_secret_value(),
                    "UserAttributes": [
                        {"Name": "email", "Value": email},
                        {"Name": "name", "Value": name},
                    ],
                },
            )
        except CognitoError as error:
            if str(error) == "UsernameExistsException":
                return False
            self.failure(error)
        return result.get("UserConfirmed") is True

    async def verify(self, email: str, code: SecretStr) -> None:
        await self.operation("ConfirmSignUp", email, ConfirmationCode=code.get_secret_value())

    async def resend(self, email: str) -> None:
        await self.operation("ResendConfirmationCode", email, obscure=True)

    async def forgot(self, email: str) -> None:
        await self.operation("ForgotPassword", email, obscure=True)

    async def reset(self, email: str, code: SecretStr, password: SecretStr) -> None:
        await self.operation(
            "ConfirmForgotPassword",
            email,
            ConfirmationCode=code.get_secret_value(),
            Password=password.get_secret_value(),
        )

    async def operation(
        self, name: str, email: str, *, obscure: bool = False, **parameters: Any
    ) -> None:
        try:
            await self.call(name, {"Username": email, **parameters})
        except CognitoError as error:
            if obscure and str(error) in {
                "UserNotFoundException",
                "InvalidParameterException",
                "NotAuthorizedException",
            }:
                return
            self.failure(error)

    async def call(self, name: str, parameters: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self.http.post(
                self.endpoint,
                headers={
                    "Content-Type": "application/x-amz-json-1.1",
                    "X-Amz-Target": "AWSCognitoIdentityProviderService." + name,
                },
                json={"ClientId": self.config.client_id, **parameters},
                timeout=10,
                follow_redirects=False,
            )
            if len(response.content) > 65536:
                raise ValueError("response limit")
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError("response format")
        except (httpx.HTTPError, ValueError):
            raise IdentityUnavailableError("Sign-in service is temporarily unavailable.") from None
        if response.status_code >= 400:
            code = str(result.get("__type", "Unknown")).rsplit("#", 1)[-1]
            raise CognitoError(code)
        return result

    @staticmethod
    def failure(error: CognitoError) -> NoReturn:
        code = str(error)
        if code in {
            "TooManyRequestsException",
            "TooManyFailedAttemptsException",
            "LimitExceededException",
        }:
            raise AttemptLimitError(60) from None
        if code in {"InvalidPasswordException", "PasswordHistoryPolicyViolationException"}:
            raise FormError(
                "Use at least 12 characters with uppercase, lowercase, a number and a symbol."
            ) from None
        if code in {
            "CodeMismatchException",
            "ExpiredCodeException",
            "UserNotFoundException",
            "NotAuthorizedException",
        }:
            raise FormError(
                "The code could not be verified. Request a new code or try signing in."
            ) from None
        if code == "InvalidParameterException":
            raise FormError("Check the form fields and try again.") from None
        raise IdentityUnavailableError("Sign-in service is temporarily unavailable.") from None
