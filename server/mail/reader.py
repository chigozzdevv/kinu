import base64
import re
from datetime import UTC, datetime
from email.utils import getaddresses
from typing import Any

from core.errors import ResourceConflictError

from mail.google import Google
from mail.models import Grant, Mailbox, Sender


class Reader:
    def __init__(self, google: Google) -> None:
        self.google = google

    async def page(self, box: Mailbox, grant: Grant, cursor: dict[str, Any]) -> dict[str, Any]:
        senders = " ".join('from:"' + rule.sender + '"' for rule in box.senders)
        query = (
            "{"
            + senders
            + "}"
            + f" after:{int(cursor['since']) - 60} before:{int(cursor['through']) + 1}"
        )
        params: dict[str, Any] = {"q": query, "maxResults": 10, "includeSpamTrash": "false"}
        if cursor.get("page"):
            params["pageToken"] = cursor["page"]
        response = await self.google.api(grant, "messages", **params)
        messages = response.get("messages", [])
        if not isinstance(messages, list) or len(messages) > 10:
            raise ResourceConflictError("Gmail returned an invalid message list")
        identities = [str(item.get("id", "")) for item in messages if isinstance(item, dict)]
        if len(identities) != len(messages) or any(
            not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", identity) for identity in identities
        ):
            raise ResourceConflictError("Gmail returned an invalid message identifier")
        token = response.get("nextPageToken")
        if token is not None and (not isinstance(token, str) or not 1 <= len(token) <= 2048):
            raise ResourceConflictError("Gmail returned an invalid page token")
        return {**cursor, "messages": identities, "next": token}

    async def fetch(
        self, box: Mailbox, grant: Grant, identity: str, since: int, through: int
    ) -> tuple[bytes, Sender, datetime] | None:
        metadata = await self.google.api(
            grant, "messages/" + identity, format="metadata", metadataHeaders=["From"]
        )
        headers = metadata.get("payload", {}).get("headers", [])
        values = [
            str(item.get("value", ""))
            for item in headers
            if str(item.get("name", "")).casefold() == "from"
        ]
        addresses = getaddresses(values)
        if len(values) != 1 or len(addresses) != 1:
            return None
        rule = next(
            (item for item in box.senders if item.sender == addresses[0][1].casefold()), None
        )
        received = int(metadata.get("internalDate", "0")) // 1000
        if (
            rule is None
            or not since <= received <= through
            or int(metadata.get("sizeEstimate", 0)) > 262_144
            or set(metadata.get("labelIds", [])).intersection({"SPAM", "TRASH"})
        ):
            return None
        message = await self.google.api(grant, "messages/" + identity, format="raw")
        encoded = message.get("raw", "")
        if not isinstance(encoded, str) or len(encoded) > 349_528:
            return None
        raw = base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
        return (raw, rule, datetime.fromtimestamp(received, UTC)) if len(raw) <= 262_144 else None
