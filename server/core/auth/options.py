from dataclasses import dataclass
from urllib.parse import urlsplit

from core.auth.cognito import CognitoConfig


def validate_origin(value: str, development: bool) -> str:
    parts = urlsplit(value)
    if (
        not parts.hostname
        or parts.username
        or parts.password
        or parts.path not in {"", "/"}
        or parts.query
        or parts.fragment
        or value != value.strip()
        or not (
            parts.scheme == "https"
            or (development and parts.scheme == "http" and parts.hostname == "localhost")
        )
    ):
        raise ValueError("public URL must be an HTTPS origin or development localhost")
    host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
    port = parts.port
    suffix = f":{port}" if port and port != (443 if parts.scheme == "https" else 80) else ""
    return f"{parts.scheme}://{host}{suffix}"


@dataclass(frozen=True, slots=True)
class BrowserOptions:
    cognito: CognitoConfig
    domain: str
    origin: str

    def __post_init__(self) -> None:
        validate_origin(self.domain, False)
        validate_origin(self.origin, True)

    @property
    def secure(self) -> bool:
        return self.origin.startswith("https://")

    @property
    def cookie_name(self) -> str:
        return "__Host-kinu" if self.secure else "kinu.session"

    @property
    def login_cookie(self) -> str:
        return "__Host-kinu.login" if self.secure else "kinu.login"

    @property
    def callback_url(self) -> str:
        return f"{self.origin}/auth/callback"
