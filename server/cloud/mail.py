import asyncio
import os
from datetime import UTC, datetime
from typing import Any

import httpx
from connectors.aws import AwsClient
from connectors.aws.agent import NativeAgent
from core.config import Settings
from core.errors import ResourceConflictError, ResourceNotFoundError
from core.storage.factory import application_database
from demo.queue import Queue
from mail.assessment import Assessment
from mail.control import ControlRequest, control
from mail.google import Google
from mail.models import GoogleConfig
from mail.poll import Poller
from mail.service import Service
from mail.store import Store
from mail.vault import Vault
from pydantic import ValidationError
from rotation.queue import Dispatcher


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    try:
        return asyncio.run(execute(event))
    except Exception:
        return {"error": "unavailable", "message": "Gmail is unavailable; try again"}


async def execute(event: dict[str, Any]) -> dict[str, Any]:
    aws, config = AwsClient(), Settings()
    store = Store(aws, config.aws_region, os.environ["KINU_GMAIL_TABLE"])
    polling = event == {"kind": "poll"}
    if polling and not await store.due(int(datetime.now(UTC).timestamp())):
        return {"processed": 0}
    secret = await aws.call(
        "secretsmanager",
        "get_secret_value",
        config.aws_region,
        SecretId=os.environ["KINU_GMAIL_SECRET_ARN"],
    )
    settings = GoogleConfig.model_validate_json(secret["SecretString"])
    if event == {"operation": "status"}:
        return {"result": settings.ready}
    if not settings.ready and (polling or event.get("operation") in {"begin", "finish", "senders"}):
        return {"error": "setup-required"}
    database = application_database(config)
    await database.open()
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=False, trust_env=False) as http:
            service = Service(
                store, Google(settings, http), Vault(settings), database, config.public_url
            )
            if polling:
                demo = (
                    Queue(aws, config.aws_region, config.demo_queue_url, config.demo_organisation)
                    if config.demo_queue_url and config.demo_organisation
                    else None
                )
                dispatcher = Dispatcher(
                    database, aws, config.aws_region, config.rotation_queue_url, demo
                )
                assessment = Assessment(
                    database,
                    store,
                    NativeAgent(aws, config.aws_region, os.environ["KINU_ASSESS_FUNCTION"]),
                    dispatcher,
                    config.aws_region,
                )
                return {"processed": await Poller(service, assessment).run()}
            return {"result": await control(service, ControlRequest.model_validate(event))}
    except ResourceNotFoundError:
        return {"error": "not-found"}
    except ResourceConflictError as error:
        return {"error": "conflict", "message": str(error)}
    except ValidationError:
        return {"error": "invalid-request", "message": "Gmail request is invalid"}
    except Exception:
        return {"error": "unavailable", "message": "Gmail is unavailable; try again"}
    finally:
        await database.close()
