from typing import Protocol

from contracts import Connection

from connectors.aws.client import Role


class RoleResolver(Protocol):
    async def resolve(self, connection: Connection) -> Role: ...
