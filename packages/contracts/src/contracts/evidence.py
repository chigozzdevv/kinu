from pydantic import AwareDatetime, Field

from contracts.base import Contract, Identifier
from contracts.state import Stage


class StageProof(Contract):
    run_id: Identifier
    organisation_id: Identifier
    stage: Stage
    checks: frozenset[str] = Field(min_length=1)
    evidence_ids: tuple[Identifier, ...] = Field(min_length=1)
    actor_id: Identifier
    recorded_at: AwareDatetime
