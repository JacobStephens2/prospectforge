from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health() -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_root() -> None:
    r = client.get("/")
    assert r.status_code == 200
    assert r.json()["name"] == "ProspectForge API"


def test_openapi_emits() -> None:
    r = client.get("/openapi.json")
    assert r.status_code == 200
    spec = r.json()
    assert spec["info"]["title"] == "ProspectForge API"
    assert "/health" in spec["paths"]
