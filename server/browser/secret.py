from datetime import datetime
from typing import Protocol

from contracts import BrowserSession, RotationRun


class SecretAccessInstaller(Protocol):
    async def install(self, run: RotationRun, session: BrowserSession) -> datetime: ...
