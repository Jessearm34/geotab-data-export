from starlette.testclient import TestClient


def test_health_is_public():
    from app.main import app

    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_api_requires_auth():
    from app.main import app

    client = TestClient(app)
    response = client.get("/api/fleet-summary", follow_redirects=False)
    assert response.status_code == 303
