from enum import StrEnum


class Stage(StrEnum):
    TRIGGER = "trigger"
    PREFLIGHT = "preflight"
    PLAN = "plan"
    CREATE = "create"
    STORE = "store"
    DEPLOY = "deploy"
    VERIFY = "verify"
    ROLLOUT = "rollout"
    OBSERVE = "observe"
    APPROVAL = "approval"
    REVOKE = "revoke"
    COMPLETE = "complete"

    @classmethod
    def _missing_(cls, value: object) -> "Stage | None":
        if value == "playbook":
            return cls.PLAN
        return None


STAGES: tuple[Stage, ...] = tuple(Stage)


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    RECOVERING = "recovering"
    CLEANUP = "cleanup-required"
    FAILED = "failed"
    CANCELLED = "cancelled"
    COMPENSATED = "compensated"
    COMPLETED = "completed"


class GenerationState(StrEnum):
    CREATING = "creating"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    REVOKED = "revoked"
    ORPHANED = "orphaned"
    UNKNOWN = "unknown"
