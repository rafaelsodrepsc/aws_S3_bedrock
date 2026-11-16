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


REVIEW = "revisao/0b8f3c2e-4a5d-4e6f-8a9b-0c1d2e3f4a5b.pdf"


def put_pending(s3, body):
    s3.put_object(Bucket=BUCKET, Key=PENDING, Body=body)


def body_of(s3, key):
    return s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()


def test_review_copies_pdf_and_returns_short_link(client, aws, admin):
    put_pending(aws["s3"], b"%PDF-1.7 original")

    resp = client.post("/admin/review", json={"key": PENDING}, headers=admin)

    assert resp.status_code == 200
    url = resp.json()["url"]
    assert REVIEW in url and "X-Amz-Expires=60" in url
    assert "response-content-type=application%2Fpdf" in url
    assert body_of(aws["s3"], REVIEW) == b"%PDF-1.7 original"


def test_review_refuses_file_that_is_not_pdf(client, aws, admin):
    put_pending(aws["s3"], b"<html>nao sou pdf</html>")

    resp = client.post("/admin/review", json={"key": PENDING}, headers=admin)

    assert resp.status_code == 422
    assert keys(aws["s3"], "revisao/") == []


def test_review_missing_object_returns_404(client, admin):
    assert client.post("/admin/review", json={"key": PENDING}, headers=admin).status_code == 404


def test_approve_publishes_the_reviewed_copy(client, aws, admin):
    put_pending(aws["s3"], b"%PDF-1.7 conteudo")
    client.post("/admin/review", json={"key": PENDING}, headers=admin)

    resp = client.post("/admin/approve", json={"key": PENDING}, headers=admin)

    assert resp.status_code == 200
    assert resp.json() == {"key": APPROVED}
    assert keys(aws["s3"], "pendentes/") == []
    assert keys(aws["s3"], "revisao/") == []
    assert body_of(aws["s3"], APPROVED) == b"%PDF-1.7 conteudo"


def test_approve_without_review_is_refused(client, aws, admin):
    put_pending(aws["s3"], b"%PDF-1.7")

    resp = client.post("/admin/approve", json={"key": PENDING}, headers=admin)

    assert resp.status_code == 409
    assert "Ver" in resp.json()["detail"]
    assert keys(aws["s3"], "docs/") == []


@pytest.mark.parametrize(
    "swaps",
    [
        [b"%PDF-1.7 trocado"],
        # X, Y, X: volta ao conteúdo original, o que enganava a trava por ETag.
        [b"%PDF-1.7 malicioso", b"%PDF-1.7 conferido"],
    ],
)
def test_swaps_after_review_never_reach_docs(client, aws, admin, swaps):
    # Quem enviou ainda tem a autorização válida e troca o arquivo depois que ele foi aberto.
    put_pending(aws["s3"], b"%PDF-1.7 conferido")
    client.post("/admin/review", json={"key": PENDING}, headers=admin)
    put_pending(aws["s3"], b"%PDF-1.7 malicioso")
    for body in swaps:
        put_pending(aws["s3"], body)

    client.post("/admin/approve", json={"key": PENDING}, headers=admin)

    assert body_of(aws["s3"], APPROVED) == b"%PDF-1.7 conferido"


def test_list_pending_paginates(client, aws, admin):
    for i in range(1001):
        aws["s3"].put_object(
            Bucket=BUCKET, Key=f"pendentes/0b8f3c2e-4a5d-4e6f-8a9b-{i:012d}.pdf", Body=b"%PDF-"
        )

    assert len(client.get("/admin/pending", headers=admin).json()) == 1001


def test_review_validates_key_and_token(client, admin):
    assert client.post("/admin/review", json={"key": PENDING}).status_code == 401
    bad = client.post("/admin/review", json={"key": "docs/x.pdf"}, headers=admin)
    assert bad.status_code == 422


def test_non_ascii_token_is_rejected_not_crashing(client):
    resp = client.get("/admin/pending", headers={"x-admin-token": "tokén".encode("latin-1")})

    assert resp.status_code == 401


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


def test_reject_deletes_pending_and_review_copy(client, aws, admin):
    put_pending(aws["s3"], b"%PDF-1.7")
    client.post("/admin/review", json={"key": PENDING}, headers=admin)

    resp = client.post("/admin/reject", json={"key": PENDING}, headers=admin)

    assert resp.status_code == 200
    assert keys(aws["s3"], "pendentes/") == []
    assert keys(aws["s3"], "revisao/") == []


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
