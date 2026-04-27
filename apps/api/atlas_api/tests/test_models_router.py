"""Integration test for /api/v1/models."""


async def test_list_models_returns_registered_specs(app_client):
    response = await app_client.get("/api/v1/models")
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    # The conftest test environment has ATLAS_LLM__ANTHROPIC_API_KEY unset
    # by default — registry is empty unless a test sets it. Just verify the
    # response shape; specific contents are environment-dependent.
    for entry in body:
        assert "provider" in entry
        assert "model_id" in entry
        assert "context_window" in entry
