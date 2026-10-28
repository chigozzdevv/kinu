from connectors.base import SecretValue
from connectors.secrets.store import SecretReader
from contracts import Connection


class Reader:
    def __init__(self, local: SecretReader, aws: SecretReader) -> None:
        self.local, self.aws = local, aws

    async def access_for(self, connection: Connection, reference: str) -> SecretValue:
        return await (self.local if connection.platform == "kinu" else self.aws).access_for(
            connection, reference
        )
