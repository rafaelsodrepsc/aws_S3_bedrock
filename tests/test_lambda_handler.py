from mangum import Mangum

from app.main import create_app


def url_event(method: str, path: str) -> dict:
    # Formato do evento da Function URL (payload 2.0), como o CloudFront entrega.
    return {
        "version": "2.0",
        "routeKey": "$default",
        "rawPath": path,
        "rawQueryString": "",
        "headers": {"host": "abc.lambda-url.us-east-2.on.aws"},
        "requestContext": {
            "accountId": "anonymous",
            "apiId": "abc",
            "domainName": "abc.lambda-url.us-east-2.on.aws",
            "http": {
                "method": method,
                "path": path,
                "protocol": "HTTP/1.1",
                "sourceIp": "130.176.0.1",
                "userAgent": "Amazon CloudFront",
            },
            "requestId": "r1",
            "routeKey": "$default",
            "stage": "$default",
            "time": "20/Nov/2026:12:00:00 +0000",
            "timeEpoch": 0,
        },
        "isBase64Encoded": False,
    }


def test_serves_upload_page(settings, aws):
    handler = Mangum(create_app(settings, aws["clients"]), lifespan="off")
    resp = handler(url_event("GET", "/"), None)
    assert resp["statusCode"] == 200
    assert "<html" in resp["body"].lower()


def test_all_clients_share_the_global_upload_limit(settings, aws):
    # Sem header de IP, todos contam juntos: é o que acontece atrás do CloudFront.
    handler = Mangum(create_app(settings, aws["clients"]), lifespan="off")
    statuses = [
        handler(url_event("POST", "/upload-url"), None)["statusCode"]
        for _ in range(settings.uploads_per_minute + 1)
    ]
    assert statuses[:-1] == [200] * settings.uploads_per_minute
    assert statuses[-1] == 429
