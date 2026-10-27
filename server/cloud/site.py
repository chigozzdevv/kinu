import asyncio
import re
from typing import Any

from connectors.aws import AwsApi
from connectors.aws.client import AwsError
from fastapi import FastAPI, HTTPException, Response

_TYPES = {
    "js": "application/javascript",
    "css": "text/css",
    "svg": "image/svg+xml",
    "png": "image/png",
    "woff2": "font/woff2",
    "ico": "image/x-icon",
    "html": "text/html",
}


def install(app: FastAPI, aws: AwsApi, region: str, bucket: str) -> None:
    cached: dict[str, tuple[bytes, str]] = {}

    @app.get("/{path:path}")
    async def site(path: str) -> Response:
        if path.startswith(("v1/", "auth/", "_agent-native/", "health/")):
            raise HTTPException(404, "Route is unavailable")
        key = path if "." in path else "index.html"
        extension = key.rpartition(".")[2]
        if not re.fullmatch(r"(?:assets/)?[A-Za-z0-9_.-]+", key) or extension not in _TYPES:
            raise HTTPException(404, "Asset is unavailable")
        if key not in cached:
            try:
                result: dict[str, Any] = await aws.call(
                    "s3", "get_object", region, Bucket=bucket, Key=key
                )
            except AwsError as error:
                if error.code in {"NoSuchKey", "AccessDenied"}:
                    raise HTTPException(404, "Asset is unavailable") from None
                raise
            body = result["Body"]
            try:
                content = await asyncio.to_thread(body.read, 3_145_729)
            finally:
                body.close()
            if len(content) > 3_145_728:
                raise HTTPException(413, "Asset exceeds the hosting limit")
            if len(cached) >= 64:
                cached.clear()
            cached[key] = (content, _TYPES[extension])
        content, mime = cached[key]
        return Response(
            content,
            media_type=mime,
            headers={
                "Cache-Control": "no-cache"
                if key == "index.html"
                else "public, max-age=31536000, immutable",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
            },
        )
