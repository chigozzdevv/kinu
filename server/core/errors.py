class KinuError(Exception):
    pass


class RevisionConflictError(KinuError):
    pass


class LeaseConflictError(KinuError):
    pass


class TransitionRejectedError(KinuError):
    pass


class RunNotFoundError(KinuError):
    pass


class ActiveRunConflictError(KinuError):
    pass


class IdempotencyConflictError(KinuError):
    pass


class StorageIntegrityError(KinuError):
    pass


class OutboxLeaseError(KinuError):
    pass


class AuthenticationError(KinuError):
    pass


class IdentityUnavailableError(KinuError):
    pass


class AuthorizationError(KinuError):
    pass


class ResourceNotFoundError(KinuError):
    pass


class ResourceConflictError(KinuError):
    pass


class ResourceSetupRequiredError(KinuError):
    pass


class ApprovalError(KinuError):
    pass


class PlaybookError(KinuError):
    pass


class CapabilityError(KinuError):
    pass


class IntegrationNotConfiguredError(KinuError):
    pass
