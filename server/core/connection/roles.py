from connectors.aws import Role
from contracts import Connection, ConnectionAuthorization, ConnectionStatus

from core.connection.models import AwsLink
from core.errors import ResourceConflictError
from core.storage.database import Database
from core.storage.records import Records


class ConnectionRoles:
    def __init__(self, database: Database) -> None:
        self._records = Records(database)

    async def resolve(self, connection: Connection) -> Role:
        prefix = "aws-role://"
        reference = connection.authorization_reference or ""
        if (
            connection.authorization is not ConnectionAuthorization.WORKLOAD_IDENTITY
            or not reference.startswith(prefix)
        ):
            raise ResourceConflictError("AWS connection requires a verified role link")
        async with self._records.read() as rows:
            stored = await rows.get(Connection, connection.organisation_id, connection.id)
            link = await rows.get(AwsLink, connection.organisation_id, reference[len(prefix) :])
        if (
            stored != connection
            or stored.status is not ConnectionStatus.READY
            or link.confirmed_at is None
            or link.region != connection.region
            or connection.archived_at
        ):
            raise ResourceConflictError("AWS role link is not available")
        return link_role(link)


def link_role(link: AwsLink) -> Role:
    if link.role_arn.split(":")[4] != link.account_id:
        raise ResourceConflictError("AWS role belongs to another account")
    return Role(
        link.role_arn,
        link.external_id,
        f"kinu-{link.id[-32:]}",
        (("kinu-org", link.organisation_id),),
    )
