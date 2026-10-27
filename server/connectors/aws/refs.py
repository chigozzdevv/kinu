import re
from dataclasses import dataclass

from core.errors import ResourceConflictError


@dataclass(frozen=True, slots=True)
class SecretRef:
    arn: str
    region: str
    account: str
    version: str | None = None

    @classmethod
    def parse(cls, value: str, *, version: bool = False) -> "SecretRef":
        arn, separator, identity = value.partition("/versions/")
        match = _SECRET.fullmatch(arn)
        if (
            not match
            or (separator and not _VERSION.fullmatch(identity))
            or (version and not separator)
        ):
            raise ResourceConflictError("an exact Secrets Manager ARN and version are required")
        return cls(arn, match["region"], match["account"], identity if separator else None)

    def reference(self, version: str) -> str:
        if not _VERSION.fullmatch(version):
            raise ResourceConflictError("Secrets Manager returned an invalid version ID")
        return f"{self.arn}/versions/{version}"


def resource_allowed(resource: str, boundaries: tuple[str, ...]) -> bool:
    return any(
        resource == boundary or resource.startswith(boundary.rstrip("/") + "/")
        for boundary in boundaries
    )


_SECRET = re.compile(
    r"arn:aws:secretsmanager:(?P<region>[a-z]{2}(?:-[a-z]+)+-\d):"
    r"(?P<account>\d{12}):secret:[A-Za-z0-9/_+=.@-]+-[A-Za-z0-9]{6}"
)
_VERSION = re.compile(r"[A-Za-z0-9-]{32,64}")
