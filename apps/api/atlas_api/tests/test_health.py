"""Tests for the FastAPI app and /health endpoint."""

from httpx import ASGITransport, AsyncClient

from atlas_api.main import app


async def test_health_returns_ok():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "environment" in body
    assert "version" in body


async def test_health_environment_is_a_string():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health")
    assert isinstance(response.json()["environment"], str)


async def test_unknown_route_returns_404():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/does-not-exist")
    assert response.status_code == 404
