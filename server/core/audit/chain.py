import hashlib
import json
from datetime import datetime

GENESIS = "0" * 64


def event_hash(
    organisation_id: str,
    sequence: int,
    kind: str,
    actor_id: str,
    resource: str,
    run_id: str | None,
    payload: dict[str, str | int | float | bool | None],
    evidence_ids: tuple[str, ...],
    previous_hash: str,
    occurred_at: datetime,
    region: str,
) -> str:
    canonical = json.dumps(
        {
            "actor_id": actor_id,
            "evidence_ids": evidence_ids,
            "kind": kind,
            "occurred_at": occurred_at.isoformat(),
            "organisation_id": organisation_id,
            "payload": payload,
            "previous_hash": previous_hash,
            "region": region,
            "resource": resource,
            "run_id": run_id,
            "sequence": sequence,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()
