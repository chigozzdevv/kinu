import argparse
import asyncio
import json
from datetime import UTC, datetime
from urllib.parse import urlsplit

from connectors.base import ConnectorResponse, SecretValue
from connectors.base.errors import ConnectorError
from connectors.resend.client import Resend
from contracts import ManagedCredential
from core.auth import PrincipalGrant, Role
from core.auth.identity import AuthenticatedIdentity
from core.errors import ResourceConflictError
from core.ids import new_id
from pymongo.errors import DuplicateKeyError

from hosting.mapping import Mappings
from hosting.models import Import, Mapping
from hosting.proof import Proof, fingerprint


class Seed:
    def __init__(self, mappings: Mappings, provider: Resend, proof: Proof | None = None) -> None:
        self.mappings, self.provider = mappings, provider
        self.proof = proof or Proof()
        self.rows = mappings.versions.vault.database.collection("host_seeds")

    async def setup(
        self,
        org: str,
        host_id: str,
        provider_id: str,
        project: str,
        url: str,
        actor: str,
        *,
        resume: bool = False,
    ) -> ManagedCredential:
        hosts, vault = self.mappings.hosts, self.mappings.versions.vault
        endpoint = urlsplit(url)
        if (
            endpoint.scheme != "https"
            or not endpoint.hostname
            or endpoint.username
            or endpoint.password
            or endpoint.query
            or endpoint.fragment
        ):
            raise ResourceConflictError("Use the test app's HTTPS probe URL")
        grant, host = await hosts.account(org, host_id)
        if not host or host.platform != "render":
            raise ResourceConflictError("Choose a connected Render test service")
        projects = await hosts.projects(org, host_id, actor, "")
        if not any(
            item["id"] == project and item["name"] == "kinu-check" for item in projects["projects"]
        ):
            raise ResourceConflictError("Sandbox setup only accepts the kinu-check service")
        provider = await hosts.inventory.get_connection(org, provider_id)
        self.provider.validate(provider)
        variables = await hosts.variables(org, host_id, project)
        selected = next((item for item in variables if item.name == "RESEND_API_KEY"), None)
        if selected is None:
            raise ResourceConflictError("Add the test app's RESEND_API_KEY placeholder first")
        query = {"organisation_id": org, "host_id": host_id, "reference": selected.reference}
        await self.rows.create_index(
            [("organisation_id", 1), ("host_id", 1), ("reference", 1)], unique=True
        )
        existing = await self.rows.find_one(query)
        if existing:
            if (
                existing.get("phase") == "completed"
                and existing["provider_connection_id"] == provider_id
            ):
                return await hosts.inventory.get_credential(org, existing["credential_id"])
            if (
                not resume
                or existing["phase"] != "stored"
                or existing["provider_connection_id"] != provider_id
            ):
                raise ResourceConflictError(
                    "Sandbox setup was already attempted; review its recorded outcome"
                )
        elif resume:
            raise ResourceConflictError("No captured sandbox key is available to resume")
        adapter = hosts.adapter(grant)
        value = await adapter.read(selected)
        if value is None:
            raise ResourceConflictError("The test app must have a readable placeholder")
        with value:
            if value.bytes() != b"setup-required":
                raise ResourceConflictError(
                    "Sandbox setup never overwrites an existing workload key"
                )
        previous = await adapter.current(selected)
        if previous.state != "ready":
            raise ResourceConflictError("Deploy the test app before initializing its key")
        if existing:
            attempt = existing["id"]
            captured = await vault.get(org, attempt)
            response = ConnectorResponse(
                result={"provider_id": existing["provider_id"]},
                secret=SecretValue(captured["token"].encode()),
            )
        else:
            attempt = new_id("seed")
            try:
                await self.rows.insert_one(
                    query
                    | {
                        "id": attempt,
                        "actor_id": actor,
                        "provider_connection_id": provider_id,
                        "phase": "creating",
                        "created_at": datetime.now(UTC),
                    }
                )
            except DuplicateKeyError:
                raise ResourceConflictError("Sandbox setup is already running") from None
            response = await self.provider.issue(provider, "Kinu sandbox " + attempt[-12:])
        assert response.secret is not None
        with response.secret as secret:
            expected = fingerprint(secret.bytes())
            if not existing:
                await vault.put(org, attempt, {"token": secret.bytes().decode()})
                await self.rows.update_one(
                    query | {"id": attempt, "phase": "creating"},
                    {
                        "$set": {
                            "phase": "stored",
                            "staging_id": attempt,
                            "provider_id": response.result["provider_id"],
                        }
                    },
                )
            if await self.provider.identify(provider, secret) != response.result["provider_id"]:
                raise ResourceConflictError("Resend creation identity changed")
            current = await adapter.read(selected)
            if current is None:
                raise ResourceConflictError("Test variable changed before installation")
            with current:
                if current.bytes() != b"setup-required":
                    raise ResourceConflictError("Test variable changed before installation")
            if (
                await hosts.inventory.get_connection(org, host_id) != host
                or await hosts.inventory.get_connection(org, provider_id) != provider
            ):
                raise ResourceConflictError("Sandbox connections changed before installation")
            acquired = await self.rows.update_one(
                query | {"id": attempt, "phase": "stored"}, {"$set": {"phase": "writing"}}
            )
            if not acquired.modified_count:
                raise ResourceConflictError("Another setup or recovery changed this attempt")
            await adapter.write(selected, secret)
        await self.rows.update_one(query, {"$set": {"phase": "deploying"}})
        deployment = await adapter.deploy(selected, previous, attempt)
        await self.rows.update_one(query, {"$set": {"deployment_id": deployment.id}})
        for _ in range(150):
            deployment = await adapter.status(selected, deployment.id)
            if deployment.state == "ready":
                break
            if deployment.state == "failed":
                raise ConnectorError(
                    "sandbox-deploy", "Test deployment failed; its key is preserved"
                )
            await asyncio.sleep(2)
        else:
            raise ConnectorError(
                "sandbox-deploy", "Test deployment is pending; inspect its recorded ID"
            )
        credentials = await self.mappings.save(
            org,
            host_id,
            actor,
            Import(
                project_id=project,
                mappings=(
                    Mapping(
                        variable_reference=selected.reference,
                        provider_connection_id=provider_id,
                        provider_id=response.result["provider_id"],
                        health_url=url,
                    ),
                ),
            ),
        )
        consumer = await hosts.inventory.get_service(org, credentials[0].consumer_ids[0])
        await self.proof.check(consumer, selected.name, expected)
        await vault.database.collection("secrets").delete_one(
            {"organisation_id": org, "id": attempt}
        )
        await self.rows.update_one(
            query,
            {
                "$set": {
                    "phase": "completed",
                    "credential_id": credentials[0].id,
                }
            },
        )
        return credentials[0]


async def run(args: argparse.Namespace) -> None:
    from executor.config import ExecutorSettings
    from executor.runtime import runtime

    async with runtime(ExecutorSettings()) as service:
        members = (
            await service.database.collection("members")
            .find({"organisation_id": args.workspace})
            .to_list()
        )
        allowed = False
        for row in members:
            grant = PrincipalGrant.model_validate(row["data"])
            actor = AuthenticatedIdentity(subject=grant.subject, issuer="local").actor_id
            allowed |= actor == args.actor and grant.enabled and Role.ADMINISTRATOR in grant.roles
        if not allowed:
            raise ResourceConflictError("Choose this workspace's enabled administrator")
        if args.recover:
            from hosting.recovery import recover

            await recover(
                service.mappings,
                service.providers.resend,
                args.workspace,
                args.host,
                args.provider,
                args.project,
                args.actor,
            )
            print(json.dumps({"recovered": True}))
            return
        credential = await Seed(service.mappings, service.providers.resend).setup(
            args.workspace,
            args.host,
            args.provider,
            args.project,
            args.url,
            args.actor,
            resume=args.resume,
        )
        print(json.dumps({"credential_id": credential.id, "provider_id": credential.provider_id}))


def main() -> None:
    parser = argparse.ArgumentParser(description="Initialize the dedicated Render/Resend sandbox")
    for name in ("workspace", "host", "provider", "project", "url", "actor"):
        parser.add_argument("--" + name, required=True)
    recovery = parser.add_mutually_exclusive_group()
    recovery.add_argument(
        "--recover",
        action="store_true",
        help="Retire and archive an unused failed setup before retrying",
    )
    recovery.add_argument(
        "--resume",
        action="store_true",
        help="Recheck and install an already captured key without creating another",
    )
    args = parser.parse_args()
    try:
        asyncio.run(run(args))
    except (ConnectorError, ResourceConflictError) as error:
        parser.exit(1, str(error) + "\n")


if __name__ == "__main__":
    main()
