import base64
import json

import pytest

from app.main import RateLimiter
from app.settings import Settings


def decode_policy(post: dict) -> dict:
    return json.loads(base64.b64decode(post["fields"]["policy"]))


def test_upload_url_limits_type_size_and_prefix(client):
    resp = client.post("/upload-url")

    assert resp.status_code == 200
    post = resp.json()
    key = post["fields"]["key"]
    assert key.startswith("pendentes/") and key.endswith(".pdf")
    conditions = decode_policy(post)["conditions"]
    assert {"Content-Type": "application/pdf"} in conditions
    assert ["content-length-range", 1, 5 * 1024 * 1024] in conditions
    assert {"key": key} in conditions


def test_upload_url_generates_a_new_key_each_time(client):
    first = client.post("/upload-url").json()["fields"]["key"]
    second = client.post("/upload-url").json()["fields"]["key"]

    assert first != second


@pytest.mark.parametrize(("asked", "expected"), [(10, 10), (1, 5), (99999, 300)])
def test_upload_url_clamps_ttl(client, asked, expected):
    from datetime import datetime

    post = client.post(f"/upload-url?ttl={asked}").json()
    expiration = datetime.fromisoformat(decode_policy(post)["expiration"].replace("Z", "+00:00"))
    remaining = (expiration - datetime.now(expiration.tzinfo)).total_seconds()

    assert expected - 3 <= remaining <= expected + 1


def test_upload_url_is_rate_limited(client):
    statuses = [client.post("/upload-url").status_code for _ in range(6)]

    assert statuses == [200] * 5 + [429]


def test_per_client_limit_uses_proxy_header(settings, aws):
    from dataclasses import replace

    from fastapi.testclient import TestClient

    from app.main import create_app

    limited = replace(
        settings, uploads_per_minute=100, uploads_per_minute_per_client=2,
        client_ip_header="cf-connecting-ip",
    )
    client = TestClient(create_app(limited, aws["clients"]))

    def call(ip):
        return client.post("/upload-url", headers={"cf-connecting-ip": ip}).status_code

    assert [call("1.1.1.1") for _ in range(3)] == [200, 200, 429]
    assert call("2.2.2.2") == 200


def test_per_client_limit_is_off_without_header_setting(client):
    # Sem CLIENT_IP_HEADER, só vale o teto total (5 nos testes), como atrás do túnel.
    statuses = [client.post("/upload-url").status_code for _ in range(5)]

    assert statuses == [200] * 5


def test_rate_limiter_keeps_keys_apart():
    limiter = RateLimiter(1, clock=lambda: 0.0)

    assert limiter.allow("a") and limiter.allow("b")
    assert not limiter.allow("a")


def test_rate_limiter_caps_distinct_clients(monkeypatch):
    now = [0.0]
    limiter = RateLimiter(5, clock=lambda: now[0])
    monkeypatch.setattr(RateLimiter, "MAX_KEYS", 3)

    assert all(limiter.allow(ip) for ip in ["a", "b", "c"])
    # Cheio de clientes ativos: cliente novo é recusado, os conhecidos continuam.
    assert not limiter.allow("d")
    assert limiter.allow("a")
    # Um minuto depois os parados saem e cabe gente nova.
    now[0] = 61.0
    assert limiter.allow("d")


def test_rate_limiter_frees_slots_after_a_minute():
    now = [0.0]
    limiter = RateLimiter(2, clock=lambda: now[0])

    assert limiter.allow() and limiter.allow()
    assert not limiter.allow()
    now[0] = 60.0
    assert limiter.allow()


def test_settings_reject_short_admin_token(monkeypatch):
    for name, value in {
        "AWS_REGION": "us-east-1",
        "DOCS_BUCKET": "b",
        "KB_ID": "k",
        "DATA_SOURCE_ID": "d",
        "MODEL_ARNS": "arn:a",
        "ADMIN_TOKEN": "curto",
    }.items():
        monkeypatch.setenv(name, value)

    with pytest.raises(RuntimeError, match="ADMIN_TOKEN"):
        Settings.from_env()


def test_settings_parse_model_list(monkeypatch):
    for name, value in {
        "AWS_REGION": "us-east-1",
        "DOCS_BUCKET": "b",
        "KB_ID": "k",
        "DATA_SOURCE_ID": "d",
        "MODEL_ARNS": "arn:a, arn:b ,",
        "ADMIN_TOKEN": "x" * 16,
    }.items():
        monkeypatch.setenv(name, value)

    assert Settings.from_env().model_arns == ("arn:a", "arn:b")
