from datetime import UTC, datetime
from typing import Any

from contracts import ConsumerBinding, CredentialGeneration, GenerationState, ManagedCredential
from core.errors import ResourceConflictError, ResourceNotFoundError
from core.storage.database import Database
from core.storage.records import Records

from demo.snapshot import Snapshot


async def record(
    database: Database, state: Snapshot, target_id: str, run_id: str, created: dict[str, Any]
) -> None:
    org = state.credential.organisation_id
    async with Records(database).write(org) as rows:
        try:
            existing = await rows.get(CredentialGeneration, org, target_id)
        except ResourceNotFoundError:
            await rows.add(
                CredentialGeneration(
                    id=target_id,
                    organisation_id=org,
                    credential_id=state.credential.id,
                    provider_id=created["provider_id"],
                    scopes=state.credential.scopes,
                    state=GenerationState.CREATING,
                    attempt_id=run_id,
                    secret_reference=created["secret_reference"],
                    predecessor_id=state.current.id,
                    created_at=datetime.now(UTC),
                ),
                org,
            )
            return
        if (
            existing.provider_id != created["provider_id"]
            or existing.secret_reference != created["secret_reference"]
        ):
            raise ResourceConflictError("candidate generation identity changed")


async def discard(database: Database, org: str, target_id: str) -> None:
    async with Records(database).write(org) as rows:
        try:
            target = await rows.get(CredentialGeneration, org, target_id)
        except ResourceNotFoundError:
            return
        if target.state is GenerationState.CREATING:
            await rows.replace(
                target.model_copy(
                    update={
                        "state": GenerationState.REVOKED,
                        "revoked_at": datetime.now(UTC),
                        "revision": target.revision + 1,
                    }
                ),
                target.revision,
            )


async def promote(database: Database, state: Snapshot, target_id: str, report_id: str) -> None:
    org, now = state.credential.organisation_id, datetime.now(UTC)
    async with Records(database).write(org) as rows:
        credential = await rows.get(ManagedCredential, org, state.credential.id)
        binding = await rows.get(ConsumerBinding, org, state.binding.id)
        old = await rows.get(CredentialGeneration, org, state.current.id)
        target = await rows.get(CredentialGeneration, org, target_id)
        if credential.active_generation_id == target_id:
            if (
                binding.current_generation_id != target_id
                or old.state is not GenerationState.REVOKED
            ):
                raise ResourceConflictError("promoted generation records are inconsistent")
            return
        if (
            credential != state.credential
            or binding != state.binding
            or old.state is not GenerationState.ACTIVE
        ):
            raise ResourceConflictError("credential mapping changed during rotation")
        await rows.replace(
            credential.model_copy(
                update={
                    "active_generation_id": target_id,
                    "secret_reference": target.secret_reference,
                    "provider_id": target.provider_id,
                    "last_observed_at": now,
                    "updated_at": now,
                    "revision": credential.revision + 1,
                }
            ),
            credential.revision,
        )
        await rows.replace(
            binding.model_copy(
                update={
                    "current_generation_id": target_id,
                    "secret_reference": target.secret_reference,
                    "target_generation_id": None,
                    "verification_report_id": report_id,
                    "revision": binding.revision + 1,
                }
            ),
            binding.revision,
        )
        await rows.replace(
            old.model_copy(
                update={
                    "state": GenerationState.REVOKED,
                    "revoked_at": now,
                    "successor_id": target_id,
                    "revision": old.revision + 1,
                }
            ),
            old.revision,
        )
        await rows.replace(
            target.model_copy(
                update={
                    "state": GenerationState.ACTIVE,
                    "last_observed_at": now,
                    "revision": target.revision + 1,
                }
            ),
            target.revision,
        )
