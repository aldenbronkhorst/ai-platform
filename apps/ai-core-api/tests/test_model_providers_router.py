import uuid

from fastapi.testclient import TestClient

from app.main import app


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
    response = client.post("/model-providers", json={
        "name": f"Kimi Test {uuid.uuid4()}",
        "base_url": "https://api.moonshot.ai/v1",
        "model_name": "kimi-k2.6",
        "display_name": "Kimi K2.6",
        "api_key": "secret-value",
        "supports_tools": True,
        "context_window": 262144,
    })

    assert response.status_code == 201
    payload = response.json()
    assert stored
    assert list(stored.values()) == ["secret-value"]
    encoded = str(payload)
    assert "secret-value" not in encoded
    provider = payload["providers"][0]
    assert provider["api_key_status"] == "saved"
    assert provider["provider_type"] == "openai_compatible"
    assert provider["models"][0]["model_name"] == "kimi-k2.6"


def test_model_provider_route_selects_primary_and_fallback(monkeypatch):
    monkeypatch.setattr("app.routers.model_providers.key_vault_uri", lambda: "")

    client = TestClient(app)
    suffix = str(uuid.uuid4())
    first = client.post("/model-providers", json={
        "name": f"Primary {suffix}",
        "base_url": "https://primary.example/v1",
        "model_name": "primary-model",
        "display_name": "Primary Model",
    })
    second = client.post("/model-providers", json={
        "name": f"Fallback {suffix}",
        "base_url": "https://fallback.example/v1",
        "model_name": "fallback-model",
        "display_name": "Fallback Model",
    })

    assert first.status_code == 201
    assert second.status_code == 201
    def model_id_for(payload: dict, name: str) -> str:
        provider = next(item for item in payload["providers"] if item["name"] == name)
        return provider["models"][0]["id"]

    primary_name = f"Primary {suffix}"
    fallback_name = f"Fallback {suffix}"
    first_model_id = model_id_for(first.json(), primary_name)
    second_model_id = model_id_for(second.json(), fallback_name)

    response = client.patch("/model-providers/route", json={
        "primary_model_id": first_model_id,
        "fallback_model_id": second_model_id,
    })

    assert response.status_code == 200
    route = response.json()["route"]
    assert route["primary_model_id"] == first_model_id
    assert route["fallback_model_id"] == second_model_id


def test_model_provider_test_uses_openai_compatible_client(monkeypatch):
    async def fake_chat_completion(self, messages, temperature=0.3, max_tokens=2000, model_override=None, tools=None):
        assert self.base_url == "https://api.deepseek.com"
        assert self.deployment_name == "deepseek-v4-flash"
        assert self.api_key == "test-key"
        return {"error": False, "content": "OK"}

    monkeypatch.setattr(
        "app.routers.model_providers.ModelProviderClient.chat_completion",
        fake_chat_completion,
    )

    client = TestClient(app)
    response = client.post("/model-providers/test", json={
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "model_name": "deepseek-v4-flash",
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
