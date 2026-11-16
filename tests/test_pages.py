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
