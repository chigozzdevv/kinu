import secrets

from fastapi import HTTPException, Request

from executor.runtime import Runtime


async def authenticated(request: Request) -> Runtime:
    service: Runtime = request.app.state.service
    assert service.config.executor_token is not None
    expected = "Bearer " + service.config.executor_token.get_secret_value()
    value = request.headers.get("authorization", "")
    if not value.isascii() or not secrets.compare_digest(expected, value):
        raise HTTPException(401)
    if len(await request.body()) > 65536:
        raise HTTPException(413)
    return service
