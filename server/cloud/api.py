import os

from api.main import app
from connectors.aws import AwsClient
from mangum import Mangum

from cloud.site import install

if bucket := os.environ.get("KINU_SITE_BUCKET"):
    install(app, AwsClient(), os.environ.get("AWS_REGION", "eu-west-2"), bucket)

handler = Mangum(app, lifespan="auto")
