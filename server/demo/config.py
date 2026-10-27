import os

from contracts import Contract, Identifier
from pydantic import Field


class DemoConfig(Contract):
    organisation_id: Identifier
    credential_id: Identifier = "credential_demo"
    provider_function: str = Field(
        pattern=r"^arn:aws:lambda:[a-z0-9-]+:[0-9]{12}:function:kinu-demo-provider$"
    )
    consumer_function: str = Field(
        pattern=r"^arn:aws:lambda:[a-z0-9-]+:[0-9]{12}:function:kinu-demo-consumer$"
    )
    secret: str = Field(
        pattern=r"^arn:aws:secretsmanager:[a-z0-9-]+:[0-9]{12}:secret:[A-Za-z0-9/_+=.@-]+$"
    )
    region: str
    assessment_function: str

    @classmethod
    def from_env(cls) -> "DemoConfig":
        return cls(
            organisation_id=os.environ["KINU_DEMO_ORGANISATION"],
            provider_function=os.environ["KINU_DEMO_PROVIDER"],
            consumer_function=os.environ["KINU_DEMO_CONSUMER"],
            secret=os.environ["KINU_DEMO_KEY"],
            region=os.environ["AWS_REGION"],
            assessment_function=os.environ["KINU_ASSESS_FUNCTION"],
        )
