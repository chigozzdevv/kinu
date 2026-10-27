from typing import Any

from core.connection.service import AwsPrepared


def role_template(prepared: AwsPrepared) -> dict[str, Any]:
    connection = prepared.connection
    role_path = prepared.role_arn.split(":role/", 1)[1].split("/")
    secrets = [arn for arn in connection.allowed_resources if ":secretsmanager:" in arn]
    functions = [arn for arn in connection.allowed_resources if ":lambda:" in arn]
    statements: list[dict[str, Any]] = []
    if secrets:
        statements.append(
            {
                "Effect": "Allow",
                "Action": [
                    "secretsmanager:DescribeSecret",
                    "secretsmanager:ListSecretVersionIds",
                    "secretsmanager:GetSecretValue",
                    "secretsmanager:PutSecretValue",
                    "secretsmanager:UpdateSecretVersionStage",
                ],
                "Resource": secrets,
            }
        )
    if functions:
        statements.append(
            {
                "Effect": "Allow",
                "Action": [
                    "lambda:GetFunctionConfiguration",
                    "lambda:UpdateFunctionConfiguration",
                    "lambda:ListVersionsByFunction",
                    "lambda:PublishVersion",
                    "lambda:GetAlias",
                    "lambda:UpdateAlias",
                    "lambda:InvokeFunction",
                ],
                "Resource": functions + [arn + ":*" for arn in functions],
            }
        )
    return {
        "AWSTemplateFormatVersion": "2010-09-09",
        "Description": "Kinu access to selected secret and consumer resources",
        "Resources": {
            "ConnectionRole": {
                "Type": "AWS::IAM::Role",
                "Properties": {
                    "RoleName": role_path[-1],
                    "Path": "/" + "/".join(role_path[:-1]) + ("/" if len(role_path) > 1 else ""),
                    "AssumeRolePolicyDocument": {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Principal": {"AWS": prepared.broker_arn},
                                "Action": "sts:AssumeRole",
                                "Condition": {
                                    "StringEquals": {
                                        "sts:ExternalId": prepared.external_id,
                                        "aws:RequestTag/kinu-org": prepared.organisation_id,
                                    },
                                    "ForAllValues:StringEquals": {"aws:TagKeys": ["kinu-org"]},
                                },
                            },
                            {
                                "Effect": "Allow",
                                "Principal": {"AWS": prepared.broker_arn},
                                "Action": "sts:TagSession",
                                "Condition": {
                                    "StringEquals": {
                                        "aws:RequestTag/kinu-org": prepared.organisation_id,
                                    },
                                    "ForAllValues:StringEquals": {"aws:TagKeys": ["kinu-org"]},
                                },
                            },
                        ],
                    },
                    "Policies": [
                        {
                            "PolicyName": "selected-resources",
                            "PolicyDocument": {"Version": "2012-10-17", "Statement": statements},
                        }
                    ],
                },
            }
        },
        "Outputs": {"RoleArn": {"Value": {"Fn::GetAtt": ["ConnectionRole", "Arn"]}}},
    }
