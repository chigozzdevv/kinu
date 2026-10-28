import json
import time
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from core.errors import ResourceConflictError

from mail.assessment import Assessment
from mail.google import ReconnectError, RequestError
from mail.links import save_connection
from mail.models import Mailbox
from mail.reader import Reader
from mail.service import Service


class Poller:
    def __init__(self, service: Service, assessment: Assessment) -> None:
        self.service, self.assessment = service, assessment
        self.reader = Reader(service.google)

    async def run(self) -> int:
        owner, started, count = "poll_" + uuid4().hex, time.monotonic(), 0
        store = self.service.store
        for item in await store.due(int(datetime.now(UTC).timestamp())):
            if time.monotonic() - started > 120:
                break
            box = Mailbox.model_validate_json(item["data"])
            if not await store.claim(
                item["pk"], box.revision, owner, int(datetime.now(UTC).timestamp())
            ):
                continue
            cursor = json.loads(item.get("cursor", "{}"))
            try:
                current, stored = await store.mailbox(box.organisation_id, box.id)
                if current.revision != box.revision or current.status != "ready":
                    continue
                cursor = json.loads(stored.get("cursor", "{}"))
                grant = self.service.open_grant(box, stored["grant"])
                if grant.expires_at <= datetime.now(UTC) + timedelta(minutes=2):
                    grant = await self.service.google.refresh(grant)
                    await store.checkpoint(box, owner, cursor, self.service.seal_grant(box, grant))
                if not cursor.get("through"):
                    cursor["through"] = int(datetime.now(UTC).timestamp()) - 1
                if not cursor.get("messages"):
                    cursor = await self.reader.page(box, grant, cursor)
                    await store.checkpoint(box, owner, cursor)
                while cursor["messages"] and time.monotonic() - started < 120:
                    identity = cursor["messages"][0]
                    data = await store.message(box.organisation_id, box.id, identity)
                    if data and not data.get("done") and data.get("revision") != box.revision:
                        data = None
                    if data is None:
                        try:
                            await self.assessment.active(box)
                            message = await self.reader.fetch(
                                box,
                                grant,
                                identity,
                                max(
                                    int(cursor.get("start", cursor["since"])),
                                    int(cursor["since"]) - 60,
                                ),
                                int(cursor["through"]),
                            )
                            data = (
                                await self.assessment.prepare(box, identity, *message)
                                if message
                                else {"done": True}
                            )
                        except RequestError as error:
                            if error.status != 404:
                                raise
                            data = {"done": True}
                        except (ValueError, UnicodeError):
                            data = {"done": True}
                        await store.message(box.organisation_id, box.id, identity, data)
                    await self.assessment.process(box, identity, data)
                    cursor["messages"].pop(0)
                    count += 1
                    await store.checkpoint(box, owner, cursor)
                if not cursor["messages"]:
                    if cursor.get("next"):
                        cursor["page"], cursor["next"] = cursor["next"], None
                    else:
                        cursor = {
                            "since": cursor["through"],
                            "start": cursor.get("start", cursor["since"]),
                        }
            except ReconnectError:
                current, stored = await store.mailbox(box.organisation_id, box.id)
                if current.revision == box.revision:
                    disconnected = current.model_copy(
                        update={
                            "status": "reauthentication-required",
                            "revision": current.revision + 1,
                        }
                    )
                    await store.save(
                        disconnected, stored.get("grant"), current.revision, cursor=cursor
                    )
                    await save_connection(self.assessment.database, disconnected)
            except RequestError as error:
                if error.status == 400:
                    cursor.pop("page", None)
            except ResourceConflictError:
                pass
            finally:
                cursor["checked"] = int(datetime.now(UTC).timestamp())
                with suppress(ResourceConflictError):
                    await store.checkpoint(box, owner, cursor, release=True)
        return count
