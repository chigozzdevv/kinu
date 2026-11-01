from typing import Any

from connectors.aws import AwsClient
from connectors.aws.runtime import AwsRuntime
from connectors.base.errors import ConnectorError
from connectors.hosting.models import Variable
from contracts import Connection
from core.connection.roles import ConnectionRoles
from core.inventory.store import InventoryStore
from core.storage.database import Database
from executor.versions import Versions
from hosting.proof import Proof, fingerprint
from hosting.service import Service


class RuntimeInspector:
    def __init__(
        self,
        database: Database,
        hosts: Service,
        versions: Versions,
        proof: Proof | None = None,
    ) -> None:
        self.database, self.hosts, self.versions = database, hosts, versions
        self.inventory = InventoryStore(database)
        self.proof = proof or Proof()
        self.aws = AwsRuntime(AwsClient(), ConnectionRoles(database, managed=True))

    async def inspect(self, connection: Connection, service_name: str) -> dict[str, Any]:
        org = connection.organisation_id
        bindings = [
            binding
            for binding in await self.inventory.bindings(org)
            if binding.runtime_connection_id == connection.id
            and binding.runtime_resource == service_name
        ]
        references = {}
        if connection.platform == "aws":
            health = await self.aws.health(connection, service_name)
            if health.get("ok") is True:
                for binding in bindings:
                    if binding.secret_reference == health.get("secret_reference"):
                        references[binding.runtime_secret_name] = binding.secret_reference
            return {"ready": bool(references), "secret_references": references}
        grant, _ = await self.hosts.account(org, connection.id)
        adapter = self.hosts.adapter(grant)
        for binding in bindings:
            row = await self.database.collection("host_bindings").find_one(
                {"organisation_id": org, "binding_id": binding.id}
            )
            if not row:
                continue
            variable = Variable.model_validate(row["variable"])
            credential = await self.inventory.get_credential(org, binding.credential_id)
            store = await self.inventory.get_connection(org, credential.secret_store_connection_id)
            service = await self.inventory.get_service(org, binding.service_id)
            if (
                await self.versions.current(store, credential.secret_resource)
                != credential.secret_reference
                or (await adapter.current(variable)).state != "ready"
            ):
                continue
            expected = None
            if await self.versions.has_value(store, credential.secret_reference):
                with await self.versions.access_for(store, credential.secret_reference) as secret:
                    expected = fingerprint(secret.bytes())
            try:
                actual = await self.proof.check(
                    service,
                    variable.name,
                    expected,
                    provider_id=credential.provider_id if expected is None else None,
                )
                value = await adapter.read(variable)
                if value:
                    with value:
                        if fingerprint(value.bytes()) != actual:
                            continue
            except ConnectorError:
                continue
            references[binding.runtime_secret_name] = credential.secret_reference
        return {
            "ready": bool(bindings) and len(references) == len(bindings),
            "secret_references": references,
        }
