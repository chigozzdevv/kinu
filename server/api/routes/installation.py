from contracts import Contract
from fastapi import APIRouter, Request
from pydantic import Field, SecretStr

from api.deps import Identity, required, services

router = APIRouter(prefix="/v1/installation", tags=["settings"])


class Claim(Contract):
    token: SecretStr = Field(min_length=32, max_length=128)


@router.post("/claim")
async def claim(body: Claim, identity: Identity, request: Request) -> dict[str, bool]:
    await required(services(request).installation, "installation setup").claim(
        identity,
        body.token,
        request.app.state.config.setup_token,
    )
    return {"administrator": True}
