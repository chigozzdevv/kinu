import asyncio
import imaplib
import json
import ssl
from collections.abc import Callable

from contracts import Connection

from connectors.base.errors import ConnectorError
from connectors.secrets.store import SecretReader


class ImapInbox:
    def __init__(
        self,
        secrets: SecretReader,
        hosts: frozenset[str],
        factory: Callable[..., imaplib.IMAP4_SSL] = imaplib.IMAP4_SSL,
    ) -> None:
        self._secrets, self._hosts, self._factory = secrets, hosts, factory

    async def read(
        self, connection: Connection, host: str, after_uid: int = 0
    ) -> tuple[tuple[int, bytes], ...]:
        if host not in self._hosts or after_uid < 0 or not connection.authorization_reference:
            raise ConnectorError("mailbox-config", "mailbox host or authorization is unavailable")
        value = await self._secrets.access_for(connection, connection.authorization_reference)
        try:
            credentials = json.loads(value.bytes())
            username, password = credentials["username"], credentials["password"]
            if not isinstance(username, str) or not isinstance(password, str):
                raise ValueError("invalid mailbox credentials")
            return await asyncio.to_thread(self._read, host, username, password, after_uid)
        except (ValueError, KeyError, imaplib.IMAP4.error, OSError, UnicodeError):
            raise ConnectorError("mailbox-read", "read-only mailbox access failed") from None
        finally:
            value.clear()

    def _read(
        self, host: str, username: str, password: str, after_uid: int
    ) -> tuple[tuple[int, bytes], ...]:
        with self._factory(
            host, 993, ssl_context=ssl.create_default_context(), timeout=10
        ) as inbox:
            inbox.login(username, password)
            status, _ = inbox.select("INBOX", readonly=True)
            if status != "OK":
                raise ValueError("mailbox unavailable")
            status, result = inbox.uid("search", "UID", f"{after_uid + 1}:*")
            if status != "OK":
                raise ValueError("mailbox search failed")
            identifiers = [item for item in (result[0] or b"").split() if int(item) > after_uid][
                :10
            ]
            messages: list[tuple[int, bytes]] = []
            for identifier in identifiers:
                status, parts = inbox.uid(
                    "fetch", identifier.decode("ascii"), "(UID BODY.PEEK[]<0.262145>)"
                )
                if status != "OK":
                    raise ValueError("mailbox fetch failed")
                body = next(
                    (
                        part[1]
                        for part in parts
                        if isinstance(part, tuple) and isinstance(part[1], bytes)
                    ),
                    None,
                )
                if body and len(body) <= 262_144:
                    messages.append((int(identifier), body))
            return tuple(messages)
