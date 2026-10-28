from typing import Any

import pytest
from connectors.aws import Role
from connectors.aws.runtime import AwsRuntime
from connectors.base.errors import ConnectorError
from contracts import Connection


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class Roles:
    async def resolve(self, connection: Connection) -> Role:
        return Role("arn:aws:iam::123456789012:role/test", "external", "test")


@pytest.mark.anyio
@pytest.mark.parametrize("changed", ["environment", "configuration"])
async def test_candidate_rejects_unpublished_changes_before_mutation(
    connection: Connection, changed: str
) -> None:
    function = "arn:aws:lambda:eu-west-2:123456789012:function:consumer"
    old = connection.allowed_resources[0] + "/versions/" + "a" * 32
    candidate = connection.allowed_resources[0] + "/versions/" + "b" * 32
    connection = connection.model_copy(
        update={"roles": frozenset({"runtime"}), "allowed_resources": (function,)}
    )

    class Aws:
        async def call(
            self,
            service: str,
            operation: str,
            region: str,
            *,
            role: Role | None = None,
            **parameters: Any,
        ) -> dict[str, Any]:
            if operation == "get_alias":
                return {"FunctionVersion": "1", "RevisionId": "alias-one"}
            assert operation == "get_function_configuration"
            published = parameters.get("Qualifier") == "1"
            env = {"PROVIDER_REF": old, "UNRELATED": "live"}
            if not published and changed == "environment":
                env["UNRELATED"] = "unpublished"
            return {
                "CodeSha256": "unchanged",
                "Environment": {"Variables": env},
                "MemorySize": 1024 if not published and changed == "configuration" else 512,
            }

    with pytest.raises(ConnectorError, match="unrelated Lambda draft"):
        await AwsRuntime(Aws(), Roles()).candidate(
            connection, function, candidate, "run_one", "PROVIDER_REF", old
        )
