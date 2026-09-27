"""El HTML personal nunca debe servirse desde caché; el home (/) ya no es
cache-first en el service worker."""

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


def test_html_no_se_cachea(client):
    for ruta in ["/", "/ficha"]:
        r = client.get(ruta)
        assert "text/html" in r.headers.get("content-type", "")
        assert "no-store" in r.headers.get("cache-control", "")


def test_sw_home_es_network_first(client):
    r = client.get("/sw.js")
    assert r.status_code == 200
    js = r.text
    # "/" ya no está en el precache del shell...
    assert '"/"' not in js.split("var SHELL")[1].split("];")[0]
    # ...y tiene su propia rama network-first.
    assert 'url.pathname === "/"' in js
    # Los estáticos siguen siendo cache-first.
    assert 'caches.match(event.request)' in js
