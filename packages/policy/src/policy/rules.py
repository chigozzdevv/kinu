from contracts import Stage

REQUIRED_CHECKS: dict[Stage, frozenset[str]] = {
    Stage.TRIGGER: frozenset(
        {
            "request-authenticated",
            "source-deduplicated",
            "lease-held",
        }
    ),
    Stage.PREFLIGHT: frozenset(
        {
            "provider-ready",
            "credential-known",
            "scopes-known",
            "playbook-eligible",
            "management-authenticated",
            "store-ready",
            "consumers-known",
            "runtime-ready",
            "verifier-ready",
            "approvers-known",
            "overlap-supported",
            "mutation-declared",
            "no-conflict",
        }
    ),
    Stage.PLAN: frozenset(
        {
            "plan-bound",
            "controls-pinned",
            "plan-hashed",
            "recovery-ready",
        }
    ),
    Stage.CREATE: frozenset(
        {
            "replacement-created",
            "mutation-resolved",
            "generation-recorded",
        }
    ),
    Stage.STORE: frozenset(
        {
            "secret-stored",
            "consumer-accessible",
            "plaintext-isolated",
        }
    ),
    Stage.DEPLOY: frozenset(
        {
            "candidate-deployed",
            "version-bound",
            "generation-tagged",
            "rollback-ready",
        }
    ),
    Stage.VERIFY: frozenset(
        {
            "provider-valid",
            "store-valid",
            "deployment-valid",
            "telemetry-healthy",
            "coverage-complete",
            "rollback-ready",
        }
    ),
    Stage.ROLLOUT: frozenset({"production-promoted", "rollout-healthy"}),
    Stage.OBSERVE: frozenset(
        {
            "telemetry-healthy",
            "old-use-clear",
            "consumers-current",
        }
    ),
    Stage.APPROVAL: frozenset(
        {
            "approval-valid",
            "action-digest-valid",
            "evidence-current",
        }
    ),
    Stage.REVOKE: frozenset(
        {
            "old-revoked",
            "replacement-valid",
            "old-rejected",
            "old-secret-disabled",
        }
    ),
    Stage.COMPLETE: frozenset(
        {
            "consumers-current",
            "replacement-valid",
            "old-rejected",
            "audit-complete",
        }
    ),
}
