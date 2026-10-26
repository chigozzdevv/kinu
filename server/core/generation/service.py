from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from contracts import CredentialGeneration, GenerationState

from core.errors import ResourceConflictError


class GenerationRepository(Protocol):
    async def create(self, generation: CredentialGeneration) -> CredentialGeneration: ...

    async def promote(
        self,
        organisation_id: str,
        credential_id: str,
        target_id: str,
        predecessor_id: str,
        report_id: str,
        binding_ids: tuple[str, ...],
    ) -> CredentialGeneration: ...

    async def revoke(
        self,
        organisation_id: str,
        generation_id: str,
        verification_report_id: str,
        revoked_at: datetime,
    ) -> CredentialGeneration: ...

    async def orphan(
        self,
        organisation_id: str,
        generation_id: str,
    ) -> CredentialGeneration: ...

    async def stage_bindings(
        self,
        organisation_id: str,
        credential_id: str,
        target_id: str,
        secret_reference: str,
        binding_ids: tuple[str, ...],
    ) -> tuple[str, ...]: ...

    async def verify_bindings(
        self,
        organisation_id: str,
        target_id: str,
        report_id: str,
        binding_ids: tuple[str, ...],
    ) -> tuple[str, ...]: ...


class GenerationService:
    def __init__(
        self,
        repository: GenerationRepository,
        clock: Callable[[], datetime],
    ) -> None:
        self._repository = repository
        self._clock = clock

    async def create(self, generation: CredentialGeneration) -> CredentialGeneration:
        if generation.state is not GenerationState.CREATING:
            raise ResourceConflictError("new rotation generations must start in creating state")
        if generation.provider_id is None or generation.secret_reference is None:
            raise ResourceConflictError(
                "a stored rotation generation requires provider and secret references"
            )
        return await self._repository.create(generation)

    async def promote(
        self,
        organisation_id: str,
        credential_id: str,
        target_id: str,
        predecessor_id: str,
        report_id: str,
        binding_ids: tuple[str, ...],
    ) -> CredentialGeneration:
        if not binding_ids:
            raise ResourceConflictError("generation promotion requires verified consumer bindings")
        return await self._repository.promote(
            organisation_id,
            credential_id,
            target_id,
            predecessor_id,
            report_id,
            binding_ids,
        )

    async def revoke(
        self,
        organisation_id: str,
        generation_id: str,
        verification_report_id: str,
    ) -> CredentialGeneration:
        return await self._repository.revoke(
            organisation_id,
            generation_id,
            verification_report_id,
            self._clock(),
        )

    async def orphan(self, organisation_id: str, generation_id: str) -> CredentialGeneration:
        return await self._repository.orphan(organisation_id, generation_id)

    async def stage_bindings(
        self,
        organisation_id: str,
        credential_id: str,
        target_id: str,
        secret_reference: str,
        binding_ids: tuple[str, ...],
    ) -> tuple[str, ...]:
        if not binding_ids:
            raise ResourceConflictError("deployment requires every consumer binding")
        return await self._repository.stage_bindings(
            organisation_id,
            credential_id,
            target_id,
            secret_reference,
            binding_ids,
        )

    async def verify_bindings(
        self,
        organisation_id: str,
        target_id: str,
        report_id: str,
        binding_ids: tuple[str, ...],
    ) -> tuple[str, ...]:
        if not binding_ids:
            raise ResourceConflictError("verification requires every consumer binding")
        return await self._repository.verify_bindings(
            organisation_id, target_id, report_id, binding_ids
        )
