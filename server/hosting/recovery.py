from datetime import UTC, datetime

from connectors.resend.client import Resend
from core.errors import ResourceConflictError

from hosting.mapping import Mappings


async def recover(
    mappings: Mappings,
    provider: Resend,
    org: str,
    host_id: str,
    provider_id: str,
    project: str,
    actor: str,
) -> None:
    hosts, private = mappings.hosts, mappings.versions.vault.database
    grant, host = await hosts.account(org, host_id)
    if not host or host.platform != "render":
        raise ResourceConflictError("Choose the connected Render sandbox")
    projects = await hosts.projects(org, host_id, actor, "")
    if not any(
        item["id"] == project and item["name"] == "kinu-check" for item in projects["projects"]
    ):
        raise ResourceConflictError("Recovery only accepts the kinu-check service")
    variable = next(
        (
            item
            for item in await hosts.variables(org, host_id, project)
            if item.name == "RESEND_API_KEY"
        ),
        None,
    )
    if variable is None:
        raise ResourceConflictError("Sandbox variable is unavailable")
    query = {"organisation_id": org, "host_id": host_id, "reference": variable.reference}
    rows = private.collection("host_seeds")
    row = await rows.find_one(query)
    if (
        not row
        or row["phase"] not in {"creating", "stored"}
        or row["provider_connection_id"] != provider_id
    ):
        raise ResourceConflictError("This setup needs a separate recovery review")
    if await hosts.database.collection("host_bindings").find_one(
        {
            "organisation_id": org,
            "connection_id": host_id,
            "reference": variable.reference,
        }
    ):
        raise ResourceConflictError("A managed workload cannot be reset by sandbox recovery")
    source = await hosts.inventory.get_connection(org, provider_id)
    name = "Kinu sandbox " + row["id"][-12:]
    matches = [
        item
        for item in await provider.metadata(source)
        if item.get("name") == name and item["status"] == "active"
    ]
    if len(matches) != 1:
        raise ResourceConflictError("Creation ownership is uncertain; do not repeat it")
    adapter = hosts.adapter(grant)
    value = await adapter.read(variable)
    if value is None:
        raise ResourceConflictError("Sandbox placeholder cannot be verified")
    with value:
        if value.bytes() != b"setup-required":
            raise ResourceConflictError("A key was installed; preserve it for workload recovery")
    owned = await rows.update_one(
        query | {"id": row["id"], "phase": row["phase"]}, {"$set": {"phase": "recovering"}}
    )
    if not owned.modified_count:
        raise ResourceConflictError("Another recovery changed this setup")
    identity = str(matches[0]["provider_id"])
    await provider.revoke_credential(source, identity)
    if any(item["provider_id"] == identity for item in await provider.metadata(source)):
        raise ResourceConflictError("Resend did not confirm the unused key's retirement")
    async with private.transaction() as session:
        await private.collection("host_seed_attempts").insert_one(
            {
                **row,
                "phase": "retired",
                "provider_id": identity,
                "recovered_by": actor,
                "recovered_at": datetime.now(UTC),
            },
            session=session,
        )
        await private.collection("secrets").delete_one(
            {"organisation_id": org, "id": row["id"]}, session=session
        )
        await rows.delete_one(query | {"id": row["id"], "phase": "recovering"}, session=session)
