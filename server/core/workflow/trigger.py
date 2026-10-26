from datetime import datetime

from contracts import CreateRunCommand, RotationRun


def build_run(command: CreateRunCommand, run_id: str, now: datetime) -> RotationRun:
    return RotationRun(
        id=run_id,
        organisation_id=command.organisation_id,
        credential_id=command.credential_id,
        trigger=command.trigger,
        control_version=command.control_version,
        created_at=now,
        updated_at=now,
    )
