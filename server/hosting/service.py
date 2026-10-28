import secrets
from datetime import UTC, datetime
from time import time
from typing import Any
from urllib.parse import quote

import httpx
from connectors.hosting.base import Hosting
from connectors.hosting.client import Client
from connectors.hosting.models import Variable
from connectors.hosting.railway import Railway
from connectors.hosting.render import Render
from connectors.hosting.vercel import Vercel
from contracts import (
    Connection,
    ConnectionAuthorization,
    ConnectionInterface,
    ConnectionRole,
    ConnectionStatus,
)
from core.errors import ResourceConflictError, ResourceNotFoundError
from core.ids import new_id
from core.inventory.store import InventoryStore
from core.storage.database import Database
from executor.vault import Vault

from hosting.grants import Grants
from hosting.models import Begin, Platform, Selection
from hosting.oauth import OAuth

TOOLS = frozenset(
    {
        "runtime.inspectSecretBindings",
        "runtime.deployCandidate",
        "runtime.shiftTraffic",
        "runtime.rollback",
    }
)


def project_ref(platform: str, identity: str) -> str:
    return platform + "://" + quote(identity, safe="")


class Service:
    def __init__(
        self, database: Database, vault: Vault, http: httpx.AsyncClient, origin: str
    ) -> None:
        self.database, self.vault, self.http, self.origin = database, vault, http, origin
        self.grants, self.inventory = Grants(vault), InventoryStore(database)

    async def begin(self, org: str, actor: str, session: str, body: Begin) -> dict[str, str]:
        identity = new_id("connection")
        details = {"platform": body.platform, "display_name": body.display_name, "id": identity}
        if body.platform == "render":
            if not body.token or not body.token.get_secret_value().strip():
                raise ResourceConflictError("Enter a Render API key")
            grant = {"access_token": body.token.get_secret_value()}
            await Render(
                Client(self.http, grant["access_token"], "https://api.render.com")
            ).projects()
            await self._draft(org, actor, session, details | grant)
            return {"connection_id": identity}
        state = secrets.token_urlsafe(32)
        settings = await self.vault.integration(body.platform)
        url, verifier = OAuth(self.http, body.platform, settings, self.origin).begin(state)
        await self.grants.begin(
            state,
            org,
            actor,
            session,
            details | {"verifier": verifier, "client_id": settings["client_id"]},
        )
        return {"authorization_url": url}

    async def owner(self, platform: Platform, state: str, actor: str, session: str) -> str:
        return str((await self.grants.owner(state, actor, session, platform))["organisation_id"])

    async def finish(
        self, org: str, platform: Platform, state: str, actor: str, session: str, code: str | None
    ) -> str:
        owner = await self.grants.owner(state, actor, session, platform)
        if owner["organisation_id"] != org:
            raise ResourceConflictError("Hosting authorization belongs to another workspace")
        row = await self.grants.consume(state, actor, session, platform)
        if not code:
            raise ResourceConflictError("Hosting access was not approved")
        settings = await self.vault.integration(platform)
        details = row["details"]
        if settings.get("client_id") != details["client_id"]:
            raise ResourceConflictError("Integration settings changed; connect again")
        grant = await OAuth(self.http, platform, settings, self.origin).exchange(
            code, details["verifier"]
        )
        await self._draft(
            org,
            actor,
            session,
            {key: value for key, value in details.items() if key not in {"verifier", "client_id"}}
            | grant,
        )
        return str(details["id"])

    async def _draft(self, org: str, actor: str, session: str, grant: dict[str, Any]) -> None:
        await self.grants.put(
            org,
            grant["id"],
            grant | {"actor": actor, "session": session, "draft_until": int(time()) + 3600},
        )

    async def account(
        self, org: str, identity: str, actor: str | None = None, session: str | None = None
    ) -> tuple[dict[str, Any], Connection | None]:
        row, grant = await self.grants.get(org, identity)
        if not row.get("active") and (
            grant.get("actor") != actor
            or grant.get("session") != session
            or grant.get("draft_until", 0) <= time()
        ):
            raise ResourceConflictError("Hosting setup expired; connect again")
        try:
            connection = await self.inventory.get_connection(org, identity)
            if (
                connection.archived_at
                or connection.status
                not in {ConnectionStatus.READY, ConnectionStatus.SETUP_REQUIRED}
                or connection.platform != grant["platform"]
                or connection.roles != frozenset({ConnectionRole.RUNTIME})
                or connection.authorization_reference != "hosting://" + identity
            ):
                raise ResourceConflictError("Reconnect this hosting account")
        except ResourceNotFoundError:
            connection = None
        platform = grant["platform"]
        if platform in {"railway", "vercel"}:
            grant = await self.grants.access(
                org,
                identity,
                OAuth(self.http, platform, await self.vault.integration(platform), self.origin),
            )
        return grant, connection

    def adapter(self, grant: dict[str, Any]) -> Hosting:
        platform = grant["platform"]
        origins = {
            "vercel": "https://api.vercel.com",
            "render": "https://api.render.com",
            "railway": "https://backboard.railway.com",
        }
        if platform not in origins:
            raise ResourceConflictError("Unknown hosting account")
        client = Client(
            self.http,
            grant["access_token"],
            origins[platform],
            {"teamId": grant["team_id"]} if grant.get("team_id") else None,
        )
        if platform == "vercel":
            return Vercel(client)
        if platform == "render":
            return Render(client)
        return Railway(client)

    async def projects(self, org: str, identity: str, actor: str, session: str) -> dict[str, Any]:
        grant, connection = await self.account(org, identity, actor, session)
        values = await self.adapter(grant).projects()
        return {
            "platform": grant["platform"],
            "display_name": grant["display_name"],
            "connection": connection.model_dump(mode="json") if connection else None,
            "projects": [item.model_dump() for item in values],
            "selected": [
                item.id
                for item in values
                if connection
                and project_ref(grant["platform"], item.id) in connection.allowed_resources
            ],
        }

    async def save(
        self, org: str, identity: str, actor: str, session: str, body: Selection
    ) -> Connection:
        grant, connection = await self.account(org, identity, actor, session)
        projects = await self.adapter(grant).projects()
        selected = set(body.project_ids)
        if len(selected) != len(body.project_ids) or not selected <= {item.id for item in projects}:
            raise ResourceConflictError("Choose projects available to this account")
        resources = tuple(project_ref(grant["platform"], item) for item in sorted(selected))
        now = datetime.now(UTC)
        if connection:
            if body.expected_revision != connection.revision:
                raise ResourceConflictError("Connection changed; refresh and try again")
            connection = await self.inventory.replace_connection(
                connection.model_copy(
                    update={
                        "allowed_resources": resources,
                        "revision": connection.revision + 1,
                        "updated_at": now,
                    }
                ),
                connection.revision,
            )
        else:
            if body.expected_revision is not None:
                raise ResourceConflictError("Hosting connection does not exist")
            connection = await self.inventory.add_connection(
                Connection(
                    id=identity,
                    organisation_id=org,
                    platform=grant["platform"],
                    display_name=grant["display_name"],
                    roles=frozenset({ConnectionRole.RUNTIME}),
                    interface=ConnectionInterface.API,
                    authorization=ConnectionAuthorization.API_KEY
                    if grant["platform"] == "render"
                    else ConnectionAuthorization.OAUTH,
                    authorization_reference="hosting://" + identity,
                    allowed_resources=resources,
                    capabilities=TOOLS,
                    status=ConnectionStatus.SETUP_REQUIRED,
                    region="global",
                    authenticated_at=now,
                    last_validated_at=now,
                    created_at=now,
                    updated_at=now,
                )
            )
        await self.grants.saved.update_one(
            {"organisation_id": org, "id": identity}, {"$set": {"active": True}}
        )
        if connection.status is ConnectionStatus.SETUP_REQUIRED:
            connection = await self.inventory.update_authentication(
                org,
                identity,
                connection.revision,
                connection.authorization_reference,
                ConnectionStatus.READY,
                now,
            )
        return connection

    async def variables(self, org: str, identity: str, project: str) -> tuple[Variable, ...]:
        grant, connection = await self.account(org, identity)
        if (
            not connection
            or project_ref(grant["platform"], project) not in connection.allowed_resources
        ):
            raise ResourceConflictError("Project is outside this connection")
        return await self.adapter(grant).variables(project)
