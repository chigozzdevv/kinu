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
    limit = 800_000 if request.url.path == "/incident-sources" else 65536
    if len(await request.body()) > limit:
        raise HTTPException(413)
    return service
