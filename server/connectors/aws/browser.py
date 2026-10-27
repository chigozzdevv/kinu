import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from urllib.parse import urlsplit

from boto3.session import Session
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from playwright.async_api import Page, async_playwright

from connectors.aws import AwsApi
from connectors.base.errors import ConnectorError


@dataclass(frozen=True)
class BrowserHandle:
    id: str
    page: Page


class AgentCoreBrowser:
    def __init__(self, aws: AwsApi, region: str, browser_id: str) -> None:
        self._aws, self._region, self._browser = aws, region, browser_id

    @asynccontextmanager
    async def session(self, name: str) -> AsyncIterator[BrowserHandle]:
        config = await self._aws.call(
            "bedrock-agentcore-control", "get_browser", self._region, browserId=self._browser
        )
        if config.get("recording", {}).get("enabled") is not False:
            raise ConnectorError(
                "browser-recording",
                "credential capture requires raw browser recording to be disabled",
            )
        started = await self._aws.call(
            "bedrock-agentcore",
            "start_browser_session",
            self._region,
            browserIdentifier=self._browser,
            name=name,
            sessionTimeoutSeconds=300,
            viewPort={"width": 1280, "height": 800},
        )
        identity = str(started["sessionId"])
        endpoint = str(started["streams"]["automationStream"]["streamEndpoint"])
        try:
            headers = await asyncio.to_thread(_headers, endpoint, self._region)
            async with async_playwright() as playwright:
                browser = await playwright.chromium.connect_over_cdp(
                    endpoint, headers=headers, timeout=30_000
                )
                try:
                    context = browser.contexts[0]
                    page = context.pages[0] if context.pages else await context.new_page()
                    page.set_default_timeout(15_000)
                    yield BrowserHandle(identity, page)
                finally:
                    await browser.close()
        finally:
            await self._aws.call(
                "bedrock-agentcore",
                "stop_browser_session",
                self._region,
                browserIdentifier=self._browser,
                sessionId=identity,
            )


def _headers(endpoint: str, region: str) -> dict[str, str]:
    parsed = urlsplit(endpoint)
    if (
        parsed.scheme != "wss"
        or parsed.hostname != f"bedrock-agentcore.{region}.amazonaws.com"
        or parsed.query
        or parsed.fragment
    ):
        raise ConnectorError(
            "browser-endpoint", "AgentCore returned an unexpected automation endpoint"
        )
    credentials = Session().get_credentials()
    if credentials is None:
        raise ConnectorError("browser-identity", "browser automation requires workload identity")
    request = AWSRequest(
        method="GET",
        url=endpoint.replace("wss://", "https://", 1),
        headers={"host": parsed.hostname},
    )
    SigV4Auth(credentials.get_frozen_credentials(), "bedrock-agentcore", region).add_auth(request)
    return dict(request.headers.items())
