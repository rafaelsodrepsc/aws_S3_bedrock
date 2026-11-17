import pytest


@pytest.mark.parametrize("path", ["/", "/chat", "/admin"])
def test_pages_return_html(client, path):
    resp = client.get(path)

    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


@pytest.mark.parametrize("path", ["/chat", "/admin"])
def test_admin_pages_use_session_storage_and_safe_dom(client, path):
    html = client.get(path).text

    assert "sessionStorage" in html
    assert "innerHTML" not in html
    assert "localStorage" not in html


@pytest.mark.parametrize(
    ("path", "content_type"),
    [("/static/docs.js", "javascript"), ("/static/app.css", "text/css")],
)
def test_shared_assets_are_served(client, path, content_type):
    resp = client.get(path)

    assert resp.status_code == 200
    assert content_type in resp.headers["content-type"]


@pytest.mark.parametrize("path", ["/", "/chat", "/admin", "/static/docs.js"])
def test_pages_and_shared_script_use_safe_dom(client, path):
    source = client.get(path).text

    for forbidden in (
        "innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "localStorage",
    ):
        assert forbidden not in source
