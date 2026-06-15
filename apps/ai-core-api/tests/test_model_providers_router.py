import uuid

from fastapi.testclient import TestClient

from app.main import app
from app.models.models import AIModel


def _patch_key_vault(monkeypatch):
    stored: dict[str, str] = {}

    async def fake_set_secret(name: str, value: str) -> None:
        stored[name] = value

    async def fake_get_secret(name: str) -> str:
        return stored.get(name, "")

    async def fake_delete_secret(name: str) -> None:
        stored.pop(name, None)

    monkeypatch.setattr("app.routers.model_providers.key_vault_uri", lambda: "https://vault.example")
    monkeypatch.setattr("app.routers.model_providers.set_secret_value", fake_set_secret)
    monkeypatch.setattr("app.routers.model_providers.get_secret_value", fake_get_secret)
    monkeypatch.setattr("app.routers.model_providers.delete_secret", fake_delete_secret)
    return stored


def _create_provider(client: TestClient, name: str, base_url: str = "https://provider.example/v1") -> dict:
    response = client.post("/model-providers", json={
        "name": name,
        "base_url": base_url,
    })
    assert response.status_code == 201
    provider = next(item for item in response.json()["providers"] if item["name"] == name)
    return provider


def _model_ids(provider: dict) -> set[str]:
    return {model["model_name"] for model in provider["models"]}


def test_provider_model_helpers_pick_general_chat_models():
    from app.routers.model_providers import _chat_model_sort_key

    models = [
        AIModel(display_name="Vision", model_name="provider-v1-128k-vision-preview"),
        AIModel(display_name="Large Context", model_name="provider-v1-128k"),
        AIModel(display_name="Old Model", model_name="provider-2.5"),
        AIModel(display_name="Code Model", model_name="provider-2.7-code"),
        AIModel(display_name="Auto", model_name="provider-v1-auto"),
    ]

    assert sorted(models, key=_chat_model_sort_key)[0].model_name == "provider-v1-auto"


def test_model_provider_upsert_stores_key_and_syncs_models(monkeypatch):
    stored = _patch_key_vault(monkeypatch)

    async def fake_fetch_available_models(base_url: str, api_key: str | None = None):
        from app.routers.model_providers import DiscoveredModel

        assert base_url == "https://provider.example/v1"
        assert api_key == "secret-value"
        return [
            DiscoveredModel(
                id="provider-chat-latest",
                display_name="Provider Chat Latest",
                context_window=128000,
                supports_tools=True,
                supports_json_schema=False,
            ),
            DiscoveredModel(id="provider-fast"),
        ]

    monkeypatch.setattr("app.routers.model_providers._fetch_available_models", fake_fetch_available_models)

    client = TestClient(app)
    provider_name = f"Provider Test {uuid.uuid4()}"
    response = client.post("/model-providers", json={
        "name": provider_name,
        "base_url": "https://provider.example/v1",
        "api_key": "secret-value",
    })

    assert response.status_code == 201
    payload = response.json()
    assert stored
    assert list(stored.values()) == ["secret-value"]
    assert "secret-value" not in str(payload)
    assert payload["sync"] == {"success": True, "message": "Synced 2 models.", "model_count": 2}

    provider = next(item for item in payload["providers"] if item["name"] == provider_name)
    assert provider["api_key_status"] == "saved"
    assert provider["provider_type"] == "openai_compatible"
    assert _model_ids(provider) == {"provider-chat-latest", "provider-fast"}
    assert all(model["enabled"] == "true" for model in provider["models"])
    assert payload["route"]["primary_model_id"] in {model["id"] for model in provider["models"]}


def test_model_provider_upsert_without_key_does_not_sync(monkeypatch):
    monkeypatch.setattr("app.routers.model_providers.key_vault_uri", lambda: "")

    client = TestClient(app)
    provider = _create_provider(client, f"No Key {uuid.uuid4()}")

    assert provider["api_key_status"] == "vault_not_configured"
    assert provider["models"] == []


def test_model_provider_sync_preserves_model_switches(monkeypatch):
    stored = _patch_key_vault(monkeypatch)

    async def fake_fetch_available_models(base_url: str, api_key: str | None = None):
        from app.routers.model_providers import DiscoveredModel

        assert api_key == "secret-value"
        return [
            DiscoveredModel(id="provider-chat-latest", display_name="Provider Chat Latest"),
            DiscoveredModel(id="provider-fast", display_name="Provider Fast"),
        ]

    monkeypatch.setattr("app.routers.model_providers._fetch_available_models", fake_fetch_available_models)

    client = TestClient(app)
    provider_name = f"Switch Parent {uuid.uuid4()}"
    create_response = client.post("/model-providers", json={
        "name": provider_name,
        "base_url": "https://provider.example/v1",
        "api_key": "secret-value",
    })
    assert create_response.status_code == 201
    provider = next(item for item in create_response.json()["providers"] if item["name"] == provider_name)
    model = next(item for item in provider["models"] if item["model_name"] == "provider-fast")

    toggle_response = client.patch(
        f"/model-providers/{provider['id']}/models/{model['id']}",
        json={"enabled": False},
    )
    assert toggle_response.status_code == 200
    toggled_provider = next(item for item in toggle_response.json()["providers"] if item["id"] == provider["id"])
    toggled_model = next(item for item in toggled_provider["models"] if item["id"] == model["id"])
    assert toggled_model["enabled"] == "false"

    assert stored
    resync_response = client.post("/model-providers", json={
        "provider_id": provider["id"],
        "name": provider_name,
        "base_url": "https://provider.example/v1",
        "api_key": None,
    })

    assert resync_response.status_code == 201
    resynced_provider = next(item for item in resync_response.json()["providers"] if item["id"] == provider["id"])
    resynced_model = next(item for item in resynced_provider["models"] if item["id"] == model["id"])
    assert resynced_model["enabled"] == "false"


def test_manual_model_and_discovery_routes_are_removed(monkeypatch):
    monkeypatch.setattr("app.routers.model_providers.key_vault_uri", lambda: "")

    client = TestClient(app)
    provider = _create_provider(client, f"Removed Manual {uuid.uuid4()}")

    discovery_response = client.post("/model-providers/discover", json={"provider_id": provider["id"]})
    manual_model_response = client.post(f"/model-providers/{provider['id']}/models", json={
        "model_name": "manual-model",
    })

    assert discovery_response.status_code == 404
    assert manual_model_response.status_code == 404


def test_model_provider_route_selects_primary_and_fallback(monkeypatch):
    stored = _patch_key_vault(monkeypatch)

    async def fake_fetch_available_models(base_url: str, api_key: str | None = None):
        from app.routers.model_providers import DiscoveredModel

        assert api_key == "secret-value"
        if "primary" in base_url:
            return [DiscoveredModel(id="primary-model", display_name="Primary Model")]
        return [DiscoveredModel(id="fallback-model", display_name="Fallback Model")]

    monkeypatch.setattr("app.routers.model_providers._fetch_available_models", fake_fetch_available_models)

    client = TestClient(app)
    suffix = str(uuid.uuid4())
    first_response = client.post("/model-providers", json={
        "name": f"Primary {suffix}",
        "base_url": "https://primary.example/v1",
        "api_key": "secret-value",
    })
    second_response = client.post("/model-providers", json={
        "name": f"Fallback {suffix}",
        "base_url": "https://fallback.example/v1",
        "api_key": "secret-value",
    })
    assert first_response.status_code == 201
    assert second_response.status_code == 201
    assert stored

    first_provider = next(item for item in second_response.json()["providers"] if item["name"] == f"Primary {suffix}")
    second_provider = next(item for item in second_response.json()["providers"] if item["name"] == f"Fallback {suffix}")
    first_model = first_provider["models"][0]
    second_model = second_provider["models"][0]

    response = client.patch("/model-providers/route", json={
        "primary_model_id": first_model["id"],
        "fallback_model_id": second_model["id"],
    })

    assert response.status_code == 200
    route = response.json()["route"]
    assert route["primary_model_id"] == first_model["id"]
    assert route["fallback_model_id"] == second_model["id"]


def test_model_provider_delete_removes_models_and_reconciles_route(monkeypatch):
    stored = _patch_key_vault(monkeypatch)

    async def fake_fetch_available_models(base_url: str, api_key: str | None = None):
        from app.routers.model_providers import DiscoveredModel

        assert api_key == "secret-value"
        if "primary" in base_url:
            return [DiscoveredModel(id="primary-model", display_name="Primary Model")]
        return [DiscoveredModel(id="fallback-model", display_name="Fallback Model")]

    monkeypatch.setattr("app.routers.model_providers._fetch_available_models", fake_fetch_available_models)

    client = TestClient(app)
    suffix = str(uuid.uuid4())
    first_response = client.post("/model-providers", json={
        "name": f"Delete Primary {suffix}",
        "base_url": "https://primary.example/v1",
        "api_key": "secret-value",
    })
    second_response = client.post("/model-providers", json={
        "name": f"Delete Fallback {suffix}",
        "base_url": "https://fallback.example/v1",
        "api_key": "secret-value",
    })
    assert first_response.status_code == 201
    assert second_response.status_code == 201
    assert len(stored) == 2

    first_provider = next(item for item in second_response.json()["providers"] if item["name"] == f"Delete Primary {suffix}")
    second_provider = next(item for item in second_response.json()["providers"] if item["name"] == f"Delete Fallback {suffix}")
    first_model = first_provider["models"][0]
    second_model = second_provider["models"][0]
    route_response = client.patch("/model-providers/route", json={
        "primary_model_id": first_model["id"],
        "fallback_model_id": second_model["id"],
    })
    assert route_response.status_code == 200

    delete_first_response = client.delete(f"/model-providers/{first_provider['id']}")
    assert delete_first_response.status_code == 200
    payload = delete_first_response.json()
    assert all(provider["id"] != first_provider["id"] for provider in payload["providers"])
    assert all(model["id"] != first_model["id"] for provider in payload["providers"] for model in provider["models"])
    assert payload["route"]["primary_model_id"] == second_model["id"]
    assert payload["route"]["fallback_model_id"] != first_model["id"]
    assert len(stored) == 1

    delete_second_response = client.delete(f"/model-providers/{second_provider['id']}")
    assert delete_second_response.status_code == 200
    payload = delete_second_response.json()
    assert all(provider["id"] != second_provider["id"] for provider in payload["providers"])
    route = payload["route"]
    if route:
        deleted_model_ids = {first_model["id"], second_model["id"]}
        assert route["primary_model_id"] not in deleted_model_ids
        assert route["fallback_model_id"] not in deleted_model_ids
    assert stored == {}


def test_model_provider_test_uses_openai_compatible_client(monkeypatch):
    async def fake_chat_completion(self, messages, temperature=0.3, max_tokens=2000, model_override=None, tools=None):
        assert self.base_url == "https://provider.example/v1"
        assert self.deployment_name == "provider-chat-latest"
        assert self.api_key == "test-key"
        return {"error": False, "content": "OK"}

    monkeypatch.setattr(
        "app.routers.model_providers.ModelProviderClient.chat_completion",
        fake_chat_completion,
    )

    client = TestClient(app)
    response = client.post("/model-providers/test", json={
        "name": "Provider",
        "base_url": "https://provider.example/v1",
        "model_name": "provider-chat-latest",
        "api_key": "test-key",
    })

    assert response.status_code == 200
    assert response.json()["success"] is True


def test_model_provider_settings_require_admin():
    from app.routers import model_providers

    auth = {"roles": ["AIPlatform.User"], "db_role": "user", "mode": "entra-jwt"}
    try:
        model_providers._require_admin(auth)
        raised = False
    except Exception as exc:
        raised = True
        assert getattr(exc, "status_code", None) == 403

    assert raised
    model_providers._require_admin({"roles": ["AIPlatform.Admin"], "mode": "entra-jwt"})
