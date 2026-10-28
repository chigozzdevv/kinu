import copy
import json
from datetime import UTC, datetime
from typing import Any

from core.errors import ResourceConflictError, ResourceNotFoundError
from mail.models import Mailbox
from mail.store import Store


class MemoryMail(Store):
    def __init__(self) -> None:
        self.items: dict[str, dict[str, Any]] = {}

    async def get(self, key: str) -> dict[str, Any] | None:
        return copy.deepcopy(self.items.get(key))

    async def pending(self, key: str, value: dict[str, Any]) -> None:
        self.items["pending:" + key] = copy.deepcopy(value)

    async def consume(
        self, state: str, session: str, actor: str, org: str, now: int
    ) -> dict[str, Any]:
        value = self.items.get("pending:" + state)
        if (
            not value
            or (value["binding"], value["actor"], value["organisation_id"]) != (session, actor, org)
            or value["expires"] <= now
        ):
            raise ResourceConflictError("OAuth state expired")
        return self.items.pop("pending:" + state)

    async def list(self, org: str) -> tuple[Mailbox, ...]:
        return tuple(
            Mailbox.model_validate_json(item["data"])
            for key, item in self.items.items()
            if key.startswith(f"mailbox:{org}:") and item.get("workspace")
        )

    async def mailbox(self, org: str, identity: str) -> tuple[Mailbox, dict[str, Any]]:
        item = await self.get(f"mailbox:{org}:{identity}")
        if not item:
            raise ResourceNotFoundError("mailbox missing")
        return Mailbox.model_validate_json(item["data"]), item

    async def save(
        self,
        mailbox: Mailbox,
        grant: str | None,
        previous: int | None,
        *,
        cursor: dict[str, Any] | None = None,
    ) -> None:
        key = f"mailbox:{mailbox.organisation_id}:{mailbox.id}"
        current = self.items.get(key)
        if (current["rev"] if current else None) != previous:
            raise ResourceConflictError("mailbox changed")
        self.items[key] = {
            "pk": key,
            "workspace": mailbox.organisation_id if mailbox.status != "disabled" else None,
            "data": mailbox.model_dump_json(),
            "grant": grant,
            "rev": mailbox.revision,
            "enabled": mailbox.status == "ready",
            "cursor": json.dumps(
                cursor
                or {
                    "since": int(datetime.now(UTC).timestamp()),
                    "start": int(datetime.now(UTC).timestamp()),
                }
            ),
        }

    async def due(self, now: int) -> tuple[dict[str, Any], ...]:
        return tuple(copy.deepcopy(item) for item in self.items.values() if item.get("enabled"))

    async def claim(self, key: str, revision: int, owner: str, now: int) -> bool:
        item = self.items[key]
        if not item.get("enabled") or item["rev"] != revision or item.get("lease_owner"):
            return False
        item["lease_owner"] = owner
        return True

    async def checkpoint(
        self,
        box: Mailbox,
        owner: str,
        cursor: dict[str, Any],
        grant: str | None = None,
        *,
        release: bool = False,
    ) -> None:
        item = self.items[f"mailbox:{box.organisation_id}:{box.id}"]
        if item["rev"] != box.revision or not item["enabled"] or item.get("lease_owner") != owner:
            raise ResourceConflictError("mailbox changed")
        item["cursor"] = json.dumps(cursor)
        if grant:
            item["grant"] = grant
        if release:
            item.pop("lease_owner")

    async def message(
        self, org: str, box: str, identity: str, value: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        key = f"message:{org}:{box}:{identity}"
        if value is not None:
            self.items[key] = copy.deepcopy(value)
        return copy.deepcopy(self.items.get(key))
