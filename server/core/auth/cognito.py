import re
from dataclasses import dataclass
from typing import cast

import httpx
import jwt

from core.auth.identity import AuthenticatedIdentity
from core.auth.keys import SigningKeys
from core.errors import AuthenticationError


@dataclass(frozen=True, slots=True)
class CognitoConfig:
    pool_id: str
    client_id: str

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z]{2}(?:-[a-z]+)+-\d_[A-Za-z0-9]+", self.pool_id):
            raise ValueError("Cognito user pool ID is invalid")
        if not re.fullmatch(r"[A-Za-z0-9]{1,128}", self.client_id):
            raise ValueError("Cognito application client ID is invalid")

    @property
    def issuer(self) -> str:
        region = self.pool_id.split("_", 1)[0]
        domain = "amazonaws.com.cn" if region.startswith("cn-") else "amazonaws.com"
        return f"https://cognito-idp.{region}.{domain}/{self.pool_id}"


class CognitoTokenVerifier:
    def __init__(self, config: CognitoConfig, http: httpx.AsyncClient) -> None:
        self._config = config
        self._keys = SigningKeys(f"{config.issuer}/.well-known/jwks.json", http)

    async def verify(self, token: str) -> AuthenticatedIdentity:
        if not token or len(token) > 32_768:
            raise AuthenticationError("identity token is invalid")
        try:
            header = jwt.get_unverified_header(token)
            key_id = header.get("kid")
            if header.get("alg") != "RS256" or not isinstance(key_id, str) or not key_id:
                raise AuthenticationError("identity token header is invalid")
            key = await self._keys.get(key_id)
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                issuer=self._config.issuer,
                audience=self._config.client_id,
                leeway=30,
                options={
                    "require": ["iss", "sub", "aud", "exp", "iat", "token_use"],
                    "strict_aud": True,
                },
            )
        except jwt.PyJWTError as error:
            raise AuthenticationError("identity token is invalid") from error
        return self._identity(cast(dict[str, object], claims))

    def _identity(self, claims: dict[str, object]) -> AuthenticatedIdentity:
        subject = claims.get("sub")
        email = claims.get("email")
        name = claims.get("name")
        if (
            claims.get("token_use") != "id"
            or not isinstance(subject, str)
            or not subject
            or not isinstance(email, str)
            or not email.strip()
            or claims.get("email_verified") is not True
            or (name is not None and not isinstance(name, str))
            or type(claims.get("exp")) is not int
            or type(claims.get("iat")) is not int
        ):
            raise AuthenticationError("identity token claims are invalid")
        return AuthenticatedIdentity(
            subject=f"{self._config.issuer}#{subject}",
            issuer=self._config.issuer,
            email=email,
            email_verified=True,
            display_name=name,
            connected_via="Cognito",
        )
