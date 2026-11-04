import httpx
from core.errors import IdentityUnavailableError
from pydantic import SecretStr

from emailer.templates import verification


class Resend:
    def __init__(
        self, http: httpx.AsyncClient, key: SecretStr | None, sender: str, origin: str
    ) -> None:
        self.http, self.key, self.sender, self.origin = http, key, sender, origin

    async def code(self, email: str, code: str, purpose: str, message_id: str) -> None:
        if not self.key:
            raise IdentityUnavailableError("Email delivery is not configured.")
        subject, text, html = verification(code, purpose, self.origin)
        try:
            response = await self.http.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": "Bearer " + self.key.get_secret_value(),
                    "Idempotency-Key": message_id,
                },
                json={
                    "from": self.sender,
                    "to": [email],
                    "subject": subject,
                    "text": text,
                    "html": html,
                },
                timeout=10,
                follow_redirects=False,
            )
            if not response.is_success:
                raise IdentityUnavailableError("Email could not be sent. Please try again.")
        except httpx.HTTPError:
            raise IdentityUnavailableError("Email could not be sent. Please try again.") from None
