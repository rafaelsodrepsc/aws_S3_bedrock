import importlib
import logging
from datetime import UTC, datetime
from types import ModuleType

import pytest
from botocore.exceptions import ClientError


@pytest.fixture
def auto_sync(monkeypatch: pytest.MonkeyPatch, aws: dict) -> ModuleType:
    monkeypatch.setenv("KB_ID", "KB12345678")
    monkeypatch.setenv("DATA_SOURCE_ID", "DS12345678")
    module = importlib.import_module("lambda.auto_sync.handler")
    monkeypatch.setattr(module, "kb_client", aws["clients"].kb_admin)
    return module


def s3_event(key: str = "docs%2Fregimento+universit%C3%A1rio.pdf",
             name: str = "ObjectCreated:Put") -> dict:
    return {"Records": [{
        "eventVersion": "2.1",
        "eventSource": "aws:s3",
        "awsRegion": "us-east-1",
        "eventTime": "2026-11-16T12:00:00.000Z",
        "eventName": name,
        "s3": {
            "s3SchemaVersion": "1.0",
            "configurationId": "auto-sync",
            "bucket": {"name": "bucket-de-teste", "arn": "arn:aws:s3:::bucket-de-teste"},
            "object": {"key": key, "size": 1024, "sequencer": "001"},
        },
    }]}


def ingestion_response() -> dict:
    return {"ingestionJob": {
        "knowledgeBaseId": "KB12345678", "dataSourceId": "DS12345678",
        "ingestionJobId": "JOB1234567", "status": "STARTING",
        "startedAt": datetime(2026, 11, 16, tzinfo=UTC),
        "updatedAt": datetime(2026, 11, 16, tzinfo=UTC),
    }}


@pytest.mark.parametrize("name", ["ObjectCreated:Put", "ObjectRemoved:Delete"])
def test_docs_event_starts_job(auto_sync: ModuleType, aws: dict, name: str) -> None:
    aws["admin_stub"].add_response("start_ingestion_job", ingestion_response(), {
        "knowledgeBaseId": "KB12345678", "dataSourceId": "DS12345678",
    })

    assert auto_sync.handler(s3_event(name=name), None) == {"started": True}
    aws["admin_stub"].assert_no_pending_responses()


@pytest.mark.parametrize("event", [
    s3_event("pendentes%2Fregimento.pdf"),
    s3_event("docs-antigos%2Fregimento.pdf"),
    s3_event(name="ObjectTagging:Put"),
    {"Event": "s3:TestEvent"},
])
def test_unrelated_event_is_ignored(auto_sync: ModuleType, event: dict) -> None:
    assert auto_sync.handler(event, None) == {"started": False, "reason": "evento ignorado"}


def test_multiple_records_start_one_job(auto_sync: ModuleType, aws: dict) -> None:
    event = {"Records": [
        *s3_event("pendentes%2Fregimento.pdf")["Records"],
        *s3_event()["Records"], *s3_event(name="ObjectRemoved:Delete")["Records"],
    ]}
    aws["admin_stub"].add_response("start_ingestion_job", ingestion_response())

    assert auto_sync.handler(event, None, client=aws["clients"].kb_admin) == {"started": True}
    aws["admin_stub"].assert_no_pending_responses()


def test_conflict_fails_so_lambda_retries(
    auto_sync: ModuleType, aws: dict, caplog: pytest.LogCaptureFixture,
) -> None:
    # Se o handler engolisse o conflito, o último arquivo de uma rajada ficaria fora da base.
    aws["admin_stub"].add_client_error(
        "start_ingestion_job", service_error_code="ConflictException",
    )

    with caplog.at_level(logging.INFO), pytest.raises(ClientError, match="ConflictException"):
        auto_sync.handler(s3_event(), None)

    assert "job em andamento" in caplog.text
    aws["admin_stub"].assert_no_pending_responses()


def test_other_error_propagates(auto_sync: ModuleType, aws: dict) -> None:
    aws["admin_stub"].add_client_error(
        "start_ingestion_job", service_error_code="AccessDeniedException",
    )

    with pytest.raises(ClientError, match="AccessDeniedException"):
        auto_sync.handler(s3_event(), None)
