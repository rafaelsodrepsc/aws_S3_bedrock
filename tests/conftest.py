import boto3
import pytest
from botocore.config import Config
from botocore.stub import Stubber
from fastapi.testclient import TestClient
from moto import mock_aws

from app.main import Clients, create_app
from app.settings import Settings

TOKEN = "token-de-teste-com-tamanho"
BUCKET = "bucket-de-teste"
MODEL_A = "arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-micro-v1:0"
MODEL_B = "arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-lite-v1:0"


@pytest.fixture(autouse=True)
def fake_credentials(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")


@pytest.fixture
def settings():
    return Settings(
        region="us-east-1",
        bucket=BUCKET,
        kb_id="KB12345678",
        data_source_id="DS12345678",
        model_arns=(MODEL_A, MODEL_B),
        admin_token=TOKEN,
        upload_ttl_seconds=300,
        uploads_per_minute=5,
    )


@pytest.fixture
def aws(settings):
    with mock_aws():
        # Mesma configuração de produção (Clients.from_settings).
        s3_config = Config(signature_version="s3v4", s3={"addressing_style": "virtual"})
        s3 = boto3.client("s3", region_name="us-east-1", config=s3_config)
        s3.create_bucket(Bucket=BUCKET)
        kb_admin = boto3.client("bedrock-agent", region_name="us-east-1")
        kb_runtime = boto3.client("bedrock-agent-runtime", region_name="us-east-1")
        with Stubber(kb_admin) as admin_stub, Stubber(kb_runtime) as runtime_stub:
            yield {
                "clients": Clients(s3=s3, kb_admin=kb_admin, kb_runtime=kb_runtime),
                "s3": s3,
                "admin_stub": admin_stub,
                "runtime_stub": runtime_stub,
            }


@pytest.fixture
def client(settings, aws):
    return TestClient(create_app(settings, aws["clients"]))


@pytest.fixture
def admin():
    return {"x-admin-token": TOKEN}
