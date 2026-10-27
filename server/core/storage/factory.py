from connectors.aws import AwsClient

from core.config import Settings
from core.storage.dataapi import DataApi
from core.storage.database import Database


def application_database(config: Settings) -> Database:
    if config.database_url:
        return Database(config.database_url.get_secret_value())
    if config.data_api_cluster_arn and config.data_api_secret_arn:
        return Database(
            data_api=DataApi(
                AwsClient(),
                config.aws_region,
                config.data_api_cluster_arn,
                config.data_api_secret_arn,
                config.data_api_database,
            )
        )
    raise ValueError("application storage is not configured")
