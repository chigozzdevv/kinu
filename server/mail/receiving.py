import base64
import hashlib
import hmac
import json
import re
from datetime import UTC, datetime
from email.message import EmailMessage
from html.parser import HTMLParser
from typing import Any
from uuid import UUID

import httpx
from connectors.http.transport import PublicTransport
from pydantic import SecretStr


def verify_webhook(body: bytes, headers: dict[str, str], secret: SecretStr, now: datetime) -> None:
    try:
        timestamp = int(headers["svix-timestamp"])
        if abs(now.timestamp() - timestamp) > 300:
            raise ValueError
        identity = headers["svix-id"]
        if not identity or len(identity) > 256:
            raise ValueError
        key = base64.b64decode(secret.get_secret_value().removeprefix("whsec_"), validate=True)
        if len(key) < 16:
            raise ValueError
        signed = identity.encode() + b"." + str(timestamp).encode() + b"." + body
        expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
        if not any(
            hmac.compare_digest(item, "v1," + expected)
            for item in headers["svix-signature"].split()
        ):
            raise ValueError
    except (KeyError, ValueError, UnicodeError, TypeError):
        raise ValueError("Invalid email webhook signature") from None


class Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


class Receiving:
    def __init__(self, http: httpx.AsyncClient, key: SecretStr) -> None:
        self.http, self.key = http, key

    async def get(self, identity: str) -> dict[str, Any]:
        identity = str(UUID(identity))
        async with self.http.stream(
            "GET",
            "https://api.resend.com/emails/receiving/" + identity,
            headers={"Authorization": "Bearer " + self.key.get_secret_value()},
            timeout=20,
            follow_redirects=False,
        ) as response:
            if not response.is_success:
                raise RuntimeError("Received email is temporarily unavailable")
            content = bytearray()
            async for chunk in response.aiter_bytes():
                content.extend(chunk)
                if len(content) > 1_048_576:
                    raise ValueError("Received email exceeded its limit")
        data = json.loads(content)
        if not isinstance(data, dict) or data.get("id") != identity:
            raise ValueError("Received email identity changed")
        return data

    async def raw(self, data: dict[str, Any]) -> bytes:
        raw = data.get("raw")
        url = raw.get("download_url") if isinstance(raw, dict) else None
        if isinstance(url, str) and len(url) <= 8192:
            parsed = httpx.URL(url)
            if parsed.scheme == "https" and not parsed.username and not parsed.password:
                async with (
                    httpx.AsyncClient(transport=PublicTransport(), timeout=15) as client,
                    client.stream("GET", url, follow_redirects=False) as response,
                ):
                    if response.is_success:
                        content = bytearray()
                        async for chunk in response.aiter_bytes():
                            content.extend(chunk)
                            if len(content) > 262_144:
                                break
                        else:
                            return bytes(content)
        # Large MIME attachments are never downloaded beyond the cap. Preserve the alert
        # text for review; this reconstruction cannot satisfy the original DKIM signature.
        text = data.get("text")
        if not isinstance(text, str):
            parser = Text()
            parser.feed(str(data.get("html") or "")[:100_000])
            text = " ".join(parser.parts)
        message = EmailMessage()
        for key, value in (("From", data.get("from")), ("Subject", data.get("subject"))):
            if not isinstance(value, str) or any(char in value for char in "\r\n"):
                raise ValueError("Received email has invalid headers")
            message[key] = value[:998]
        headers = data.get("headers", {})
        if isinstance(headers, dict):
            for name in ("date", "message-id"):
                value = next((v for k, v in headers.items() if k.lower() == name), None)
                if isinstance(value, str) and not any(char in value for char in "\r\n"):
                    message[name] = value[:998]
        message.set_content(text[:100_000])
        return message.as_bytes()


def confirmation(data: dict[str, Any], address: str) -> str | None:
    from email.utils import parseaddr

    sender = parseaddr(str(data.get("from", "")))[1].casefold()
    authentication = data.get("authentication")
    if sender != "forwarding-noreply@google.com" or not isinstance(authentication, dict):
        return None
    if authentication.get("dmarc") != "pass":
        return None
    text = str(data.get("text") or "") + "\n" + str(data.get("html") or "")
    if address.casefold() not in text.casefold():
        return None
    match = re.search(r"https://mail-settings\.google\.com/mail/vf-[A-Za-z0-9_?=&%.-]+", text)
    return match[0] if match else None


def now() -> datetime:
    return datetime.now(UTC)
