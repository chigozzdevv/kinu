from contracts import Connection, SetupSession
from core.private import PrivateClient


class Bridge:
    def __init__(self, private: PrivateClient, origin: str) -> None:
        self.private, self.origin = private, origin.rstrip("/")

    @property
    def setup_url(self) -> str:
        return self.origin + "/browser/setup"

    @property
    def gateway_url(self) -> str:
        return self.origin + "/browser-gateway"

    async def begin(
        self,
        organisation_id: str,
        connection_id: str,
        subject: str,
        extra_domains: tuple[str, ...] = (),
    ) -> tuple[SetupSession, str]:
        result = await self.private.call(
            "/browser",
            {
                "operation": "begin",
                "organisation_id": organisation_id,
                "connection_id": connection_id,
                "subject": subject,
                "data": {"extra_domains": list(extra_domains)},
            },
        )
        return SetupSession.model_validate(result["session"]), str(result["token"])

    async def complete(
        self,
        organisation_id: str,
        setup_id: str,
        expected_revision: int,
        token: str,
        subject: str,
        actor_id: str | None = None,
    ) -> tuple[SetupSession, Connection, tuple[str, ...]]:
        result = await self.private.call(
            "/browser",
            {
                "operation": "complete",
                "organisation_id": organisation_id,
                "setup_id": setup_id,
                "subject": subject,
                "token": token,
                "revision": expected_revision,
            },
        )
        return (
            SetupSession.model_validate(result["session"]),
            Connection.model_validate(result["connection"]),
            (),
        )

    async def abort(
        self, organisation_id: str, setup_id: str, expected_revision: int, subject: str
    ) -> SetupSession:
        return SetupSession.model_validate(
            await self.private.call(
                "/browser",
                {
                    "operation": "abort",
                    "organisation_id": organisation_id,
                    "setup_id": setup_id,
                    "subject": subject,
                    "revision": expected_revision,
                },
            )
        )

    async def get(self, organisation_id: str, setup_id: str) -> SetupSession:
        return SetupSession.model_validate(
            await self.private.call(
                "/browser",
                {"operation": "get", "organisation_id": organisation_id, "setup_id": setup_id},
            )
        )

    async def reap_expired(self, organisation_id: str) -> tuple[SetupSession, ...]:
        return tuple(
            SetupSession.model_validate(item)
            for item in await self.private.call(
                "/browser", {"operation": "reap", "organisation_id": organisation_id}
            )
        )
