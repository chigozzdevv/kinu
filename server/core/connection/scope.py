from typing import Any

from contracts import Connection

from core.connection.models import AwsLink
from core.errors import ResourceConflictError


def managed_role(link: AwsLink) -> None:
    if link.role_arn != f"arn:aws:iam::{link.account_id}:role/kinu/{link.id}":
        raise ResourceConflictError("AWS role is outside this managed connection")


def session_policy(link: AwsLink, connection: Connection, read_only: bool) -> dict[str, Any]:
    statements: list[dict[str, Any]] = []
    secrets = [
        arn for arn in connection.allowed_resources if arn.startswith("arn:aws:secretsmanager:")
    ]
    functions = [arn for arn in connection.allowed_resources if arn.startswith("arn:aws:lambda:")]
    if secrets:
        actions = [
            "secretsmanager:DescribeSecret",
            "secretsmanager:ListSecretVersionIds",
            "secretsmanager:GetSecretValue",
        ]
        if not read_only:
            actions += ["secretsmanager:PutSecretValue", "secretsmanager:UpdateSecretVersionStage"]
        statements.append({"Effect": "Allow", "Action": actions, "Resource": secrets})
    if functions:
        actions = [
            "lambda:GetFunctionConfiguration",
            "lambda:GetAlias",
            "lambda:ListVersionsByFunction",
        ]
        if not read_only:
            actions += [
                "lambda:UpdateFunctionConfiguration",
                "lambda:PublishVersion",
                "lambda:UpdateAlias",
                "lambda:InvokeFunction",
            ]
        statements.append(
            {
                "Effect": "Allow",
                "Action": actions,
                "Resource": functions + [arn + ":*" for arn in functions],
            }
        )
    if link.kms_arns:
        actions = ["kms:Decrypt"] + ([] if read_only else ["kms:GenerateDataKey"])
        statements.append(
            {
                "Effect": "Allow",
                "Action": actions,
                "Resource": list(link.kms_arns),
                "Condition": {
                    "StringEquals": {
                        "kms:ViaService": f"secretsmanager.{link.region}.amazonaws.com"
                    }
                },
            }
        )
    return {"Version": "2012-10-17", "Statement": statements}
