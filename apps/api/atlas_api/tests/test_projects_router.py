"""Integration tests for the /projects router against a real Postgres test DB."""

from uuid import uuid4


async def test_list_projects_empty(app_client):
    response = await app_client.get("/api/v1/projects")
    assert response.status_code == 200
    assert response.json() == []


async def test_create_project_minimal(app_client):
    response = await app_client.post(
        "/api/v1/projects",
        json={"name": "First", "default_model": "claude-sonnet-4-6"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "First"
    assert body["status"] == "active"
    assert body["privacy_level"] == "cloud_ok"
    assert body["enabled_plugins"] == []
    assert body["user_id"] == "matt"
    assert "id" in body
    assert "created_at" in body


async def test_create_project_with_all_fields(app_client):
    response = await app_client.post(
        "/api/v1/projects",
        json={
            "name": "Full",
            "description": "with everything",
            "privacy_level": "local_only",
            "default_model": "gemma-3-12b",
            "enabled_plugins": ["github", "gmail"],
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["description"] == "with everything"
    assert body["privacy_level"] == "local_only"
    assert body["enabled_plugins"] == ["github", "gmail"]


async def test_create_then_list_returns_one(app_client):
    await app_client.post(
        "/api/v1/projects",
        json={"name": "Listed", "default_model": "x"},
    )
    response = await app_client.get("/api/v1/projects")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["name"] == "Listed"


async def test_create_rejects_empty_name(app_client):
    response = await app_client.post(
        "/api/v1/projects",
        json={"name": "", "default_model": "x"},
    )
    assert response.status_code == 422


async def test_create_rejects_unknown_privacy_level(app_client):
    response = await app_client.post(
        "/api/v1/projects",
        json={"name": "x", "default_model": "x", "privacy_level": "not_a_value"},
    )
    assert response.status_code == 422


async def test_get_project_by_id(app_client):
    created = (
        await app_client.post(
            "/api/v1/projects",
            json={"name": "Findable", "default_model": "x"},
        )
    ).json()
    response = await app_client.get(f"/api/v1/projects/{created['id']}")
    assert response.status_code == 200
    assert response.json()["id"] == created["id"]


async def test_get_project_returns_404_for_missing_id(app_client):
    response = await app_client.get(f"/api/v1/projects/{uuid4()}")
    assert response.status_code == 404


async def test_patch_project_updates_provided_fields_only(app_client):
    created = (
        await app_client.post(
            "/api/v1/projects",
            json={"name": "Original", "description": "keep me", "default_model": "x"},
        )
    ).json()

    response = await app_client.patch(
        f"/api/v1/projects/{created['id']}",
        json={"name": "Renamed"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Renamed"
    assert body["description"] == "keep me"  # unchanged


async def test_patch_project_changes_privacy_level(app_client):
    created = (
        await app_client.post(
            "/api/v1/projects",
            json={"name": "P", "default_model": "x"},
        )
    ).json()

    response = await app_client.patch(
        f"/api/v1/projects/{created['id']}",
        json={"privacy_level": "local_only"},
    )
    assert response.status_code == 200
    assert response.json()["privacy_level"] == "local_only"


async def test_patch_returns_404_for_missing_id(app_client):
    response = await app_client.patch(f"/api/v1/projects/{uuid4()}", json={"name": "x"})
    assert response.status_code == 404


async def test_delete_project_soft_archives(app_client):
    created = (
        await app_client.post(
            "/api/v1/projects",
            json={"name": "Deletable", "default_model": "x"},
        )
    ).json()

    delete_response = await app_client.delete(f"/api/v1/projects/{created['id']}")
    assert delete_response.status_code == 204

    # Soft delete: row still exists with status='archived'
    get_response = await app_client.get(f"/api/v1/projects/{created['id']}")
    assert get_response.status_code == 200
    assert get_response.json()["status"] == "archived"


async def test_delete_returns_404_for_missing_id(app_client):
    response = await app_client.delete(f"/api/v1/projects/{uuid4()}")
    assert response.status_code == 404


async def test_patch_rejects_explicit_null_on_required_field(app_client):
    """Explicit `null` for a NOT-NULL column field returns 422, not 500."""
    created = (
        await app_client.post(
            "/api/v1/projects",
            json={"name": "P", "default_model": "x"},
        )
    ).json()

    response = await app_client.patch(
        f"/api/v1/projects/{created['id']}",
        json={"name": None},
    )
    assert response.status_code == 422
