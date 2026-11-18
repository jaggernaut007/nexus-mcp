from app.main import app
from fastapi.testclient import TestClient

client = TestClient(app)


def test_list_orders():
    assert client.get("/api/orders").status_code == 200


def test_charge():
    assert client.post("/api/payments/charge", json={}).status_code in (200, 422)
