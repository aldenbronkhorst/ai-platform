import uuid

from fastapi.testclient import TestClient

from app.main import app


def _create_provider(client: TestClient, name: str, base_url: str = "https://provider.example/v1") -> dict:
    response = client.post("/model-providers", json={
        "name": name,
        "base_url": base_url,
    })
    assert response.status_code == 201
    provider = next(item for item in response.json()["providers"] if item["name"] == name)
    return provider


def _add_model(client: TestClient, provider_id: str, model_name: str, display_name: str = "Chat Model") -> dict:
    response = client.post(f"/model-providers/{provider_id}/models", json={
        "model_name": model_name,
        "display_name": display_name,
        "supports_tools": True,
        "supports_json_schema": False,
        "context_window": 128000,
    })
    assert response.status_code == 200
    provider = next(item for item in response.json()["providers"] if item["id"] == provider_id)
    return next(item for item in provider["models"] if item["model_name"] == model_name)


def test_model_provider_upsert_stores_key_in_key_vault_and_hides_value(monkeypatch):
    stored: dict[str, str] = {}

    async def fake_set_secret(name: str, value: str) -> None:
        stored[name] = value

    async def fake_get_secret(name: str) -> str:
        return stored.get(name, "")

    monkeypatch.setattr("app.routers.model_providers.key_vault_uri", lambda: "https://vault.example")
    monkeypatch.setattr("app.routers.model_providers.set_secret_value", fake_set_secret)
    monkeypatch.setattr("app.routers.model_providers.get_secret_value", fake_get_secret)

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

    provider = next(item for item in payload["providers"] if item["name"] == provider_name)
    assert provider["api_key_status"] == "saved"
    assert provider["provider_type"] == "openai_compatible"
    assert provider["models"] == []


def test_model_provider_model_upsert_adds_model_under_provider(monkeypatch):
    monkeypatch.setattr("app.routers.model_providers.key_vault_uri", lambda: "")

    client = TestClient(app)
    provider = _create_provider(client, f"Model Parent {uuid.uuid4()}")
    model = _add_model(client, provider["id"], "provider-chat-latest", "Provider Chat Latest")

    assert model["display_name"] == "Provider Chat Latest"
    assert model["model_name"] == "provider-chat-latest"
    assert model["deployment_name"] == "provider-chat-latest"
    assert model["supports_tools"] == "true"


def test_model_provider_discovery_returns_provider_models(monkeypatch):
    monkeypatch.setattr("app.routers.model_providers.key_vault_uri", lambda: "")

    async def fake_fetch_available_models(base_url: str, api_key: str | None = None):
        from app.routers.model_providers import DiscoveredModel

        assert base_url == "https://provider.example/v1"
        assert api_key is None
        return [
            DiscoveredModel(
                id="provider-chat-latest",
                display_name="Provider Chat Latest",
                context_window=128000,
                supports_tools=True,
                supports_json_schema=False,
            )
        ]

    monkeypatch.setattr("app.routers.model_providers._fetch_available_models", fake_fetch_available_models)

    client = TestClient(app)
    provider = _create_provider(client, f"Discovery Parent {uuid.uuid4()}")
    response = client.post("/model-providers/discover", json={"provider_id": provider["id"]})

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["models"][0]["id"] == "provider-chat-latest"


def test_model_provider_route_selects_primary_and_fallback(monkeypatch):
    monkeypatch.setattr("app.routers.model_providers.key_vault_uri", lambda: "")

    client = TestClient(app)
    suffix = str(uuid.uuid4())
    first_provider = _create_provider(client, f"Primary {suffix}", "https://primary.example/v1")
    second_provider = _create_provider(client, f"Fallback {suffix}", "https://fallback.example/v1")
    first_model = _add_model(client, first_provider["id"], "primary-model", "Primary Model")
    second_model = _add_model(client, second_provider["id"], "fallback-model", "Fallback Model")

    response = client.patch("/model-providers/route", json={
        "primary_model_id": first_model["id"],
        "fallback_model_id": second_model["id"],
    })

    assert response.status_code == 200
    route = response.json()["route"]
    assert route["primary_model_id"] == first_model["id"]
    assert route["fallback_model_id"] == second_model["id"]


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
