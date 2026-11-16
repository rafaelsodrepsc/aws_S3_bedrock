import pytest

from tests.conftest import BUCKET

PENDING = "pendentes/0b8f3c2e-4a5d-4e6f-8a9b-0c1d2e3f4a5b.pdf"
APPROVED = "docs/0b8f3c2e-4a5d-4e6f-8a9b-0c1d2e3f4a5b.pdf"


def keys(s3, prefix):
    return [o["Key"] for o in s3.list_objects_v2(Bucket=BUCKET, Prefix=prefix).get("Contents", [])]


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/admin/pending"),
        ("post", "/admin/sync"),
        ("get", "/admin/sync/JOB"),
    ],
)
@pytest.mark.parametrize("token", [None, "errado", ""])
def test_admin_routes_require_token(client, method, path, token):
    headers = {} if token is None else {"x-admin-token": token}

    resp = getattr(client, method)(path, headers=headers)

    assert resp.status_code == 401


def test_ask_and_search_require_token(client):
    assert client.post("/ask", json={"question": "abc"}).status_code == 401
    assert client.post("/search", json={"question": "abc"}).status_code == 401


def test_list_pending(client, aws, admin):
    aws["s3"].put_object(Bucket=BUCKET, Key=PENDING, Body=b"%PDF-1.7")
    aws["s3"].put_object(Bucket=BUCKET, Key=APPROVED, Body=b"%PDF-1.7")

    resp = client.get("/admin/pending", headers=admin)

    assert resp.status_code == 200
    assert [p["key"] for p in resp.json()] == [PENDING]


def test_approve_moves_pdf_to_docs(client, aws, admin):
    aws["s3"].put_object(Bucket=BUCKET, Key=PENDING, Body=b"%PDF-1.7 conteudo")

    resp = client.post("/admin/approve", json={"key": PENDING}, headers=admin)

    assert resp.status_code == 200
    assert resp.json() == {"key": APPROVED}
    assert keys(aws["s3"], "pendentes/") == []
    assert keys(aws["s3"], "docs/") == [APPROVED]


def test_approve_refuses_file_that_is_not_pdf(client, aws, admin):
    aws["s3"].put_object(Bucket=BUCKET, Key=PENDING, Body=b"<html>nao sou pdf</html>")

    resp = client.post("/admin/approve", json={"key": PENDING}, headers=admin)

    assert resp.status_code == 422
    assert keys(aws["s3"], "pendentes/") == [PENDING]
    assert keys(aws["s3"], "docs/") == []


def test_approve_missing_object_returns_404(client, admin):
    resp = client.post("/admin/approve", json={"key": PENDING}, headers=admin)

    assert resp.status_code == 404


@pytest.mark.parametrize(
    "key",
    [
        "docs/0b8f3c2e-4a5d-4e6f-8a9b-0c1d2e3f4a5b.pdf",
        "pendentes/../docs/x.pdf",
        "pendentes/0b8f3c2e-4a5d-4e6f-8a9b-0c1d2e3f4a5b.pdf.exe",
        "pendentes/nome-qualquer.pdf",
    ],
)
def test_approve_and_reject_validate_key(client, admin, key):
    assert client.post("/admin/approve", json={"key": key}, headers=admin).status_code == 422
    assert client.post("/admin/reject", json={"key": key}, headers=admin).status_code == 422


def test_reject_deletes_pending(client, aws, admin):
    aws["s3"].put_object(Bucket=BUCKET, Key=PENDING, Body=b"%PDF-1.7")

    resp = client.post("/admin/reject", json={"key": PENDING}, headers=admin)

    assert resp.status_code == 200
    assert keys(aws["s3"], "pendentes/") == []


def test_start_sync(client, aws, admin):
    aws["admin_stub"].add_response(
        "start_ingestion_job",
        {"ingestionJob": {
            "knowledgeBaseId": "KB12345678",
            "dataSourceId": "DS12345678",
            "ingestionJobId": "JOB1234567",
            "status": "STARTING",
            "startedAt": "2026-11-16T12:00:00Z",
            "updatedAt": "2026-11-16T12:00:00Z",
        }},
        {"knowledgeBaseId": "KB12345678", "dataSourceId": "DS12345678"},
    )

    resp = client.post("/admin/sync", headers=admin)

    assert resp.status_code == 200
    assert resp.json() == {"job_id": "JOB1234567", "status": "STARTING"}


def test_sync_already_running_returns_409(client, aws, admin):
    aws["admin_stub"].add_client_error(
        "start_ingestion_job", service_error_code="ConflictException"
    )

    resp = client.post("/admin/sync", headers=admin)

    assert resp.status_code == 409
    assert "ConflictException" in resp.json()["detail"]


def test_sync_status(client, aws, admin):
    aws["admin_stub"].add_response(
        "get_ingestion_job",
        {"ingestionJob": {
            "knowledgeBaseId": "KB12345678",
            "dataSourceId": "DS12345678",
            "ingestionJobId": "JOB1234567",
            "status": "COMPLETE",
            "statistics": {"numberOfNewDocumentsIndexed": 2},
            "startedAt": "2026-11-16T12:00:00Z",
            "updatedAt": "2026-11-16T12:01:00Z",
        }},
        {"knowledgeBaseId": "KB12345678", "dataSourceId": "DS12345678",
         "ingestionJobId": "JOB1234567"},
    )

    resp = client.get("/admin/sync/JOB1234567", headers=admin)

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "COMPLETE"
    assert body["statistics"]["numberOfNewDocumentsIndexed"] == 2
