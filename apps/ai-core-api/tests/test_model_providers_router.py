import uuid
from datetime import datetime, timezone
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import delete, select, text, update

from app.main import app
from app.models.models import AIModel, AIProvider, AIRoute, AITrace
from tests.conftest import TestingSessionLocal


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


def _create_provider(
    client: TestClient,
    name: str,
    base_url: str = "https://provider.example/v1",
    enabled: bool = False,
) -> dict:
    response = client.post("/model-providers", json={
        "name": name,
        "base_url": base_url,
        "enabled": enabled,
    })
    assert response.status_code == 201
    provider = next(item for item in response.json()["providers"] if item["name"] == name)
    return provider


def _model_ids(provider: dict) -> set[str]:
    return {model["model_name"] for model in provider["models"]}


def _add_route(task_type: str, primary_model_id: str) -> None:
    import asyncio

    async def add_route() -> None:
        async with TestingSessionLocal() as session:
            session.add(AIRoute(
                id=uuid.uuid4(),
                task_type=task_type,
                primary_model_id=uuid.UUID(primary_model_id),
                temperature=Decimal("0.30"),
                max_tokens=2000,
                enabled="true",
            ))
            await session.commit()

    asyncio.run(add_route())


def _route_model_id(task_type: str) -> str | None:
    import asyncio

    async def read_route() -> str | None:
        async with TestingSessionLocal() as session:
            result = await session.execute(select(AIRoute).where(AIRoute.task_type == task_type))
            route = result.scalar_one_or_none()
            if not route:
                return None
            return str(route.primary_model_id)

    return asyncio.run(read_route())


def _route_id(task_type: str) -> uuid.UUID | None:
    import asyncio

    async def read_route_id() -> uuid.UUID | None:
        async with TestingSessionLocal() as session:
            result = await session.execute(select(AIRoute).where(AIRoute.task_type == task_type))
            route = result.scalar_one_or_none()
            return route.id if route else None

    return asyncio.run(read_route_id())


def _add_trace_for_route(route_id: uuid.UUID) -> str:
    import asyncio

    trace_id = f"trace-{uuid.uuid4()}"

    async def add_trace() -> None:
        async with TestingSessionLocal() as session:
            session.add(AITrace(
                id=uuid.uuid4(),
                trace_id=trace_id,
                request_id=f"request-{uuid.uuid4()}",
                operation_type="chat",
                operation_name="chat",
                status="success",
                route_id=route_id,
                started_at=datetime.now(timezone.utc),
            ))
            await session.commit()

    asyncio.run(add_trace())
    return trace_id


def _trace_route_id(trace_id: str) -> uuid.UUID | None:
    import asyncio

    async def read_trace_route_id() -> uuid.UUID | None:
        async with TestingSessionLocal() as session:
            result = await session.execute(select(AITrace).where(AITrace.trace_id == trace_id))
            trace = result.scalar_one_or_none()
            return trace.route_id if trace else None

    return asyncio.run(read_trace_route_id())


def _disable_other_providers(provider_id: str) -> None:
    import asyncio

    async def disable_providers() -> None:
        async with TestingSessionLocal() as session:
            await session.execute(
                update(AIProvider)
                .where(AIProvider.id != uuid.UUID(provider_id))
                .values(enabled="false")
            )
            await session.commit()

    asyncio.run(disable_providers())


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


def test_model_provider_upsert_enabled_without_key_is_rejected(monkeypatch):
    monkeypatch.setattr("app.routers.model_providers.key_vault_uri", lambda: "")

    client = TestClient(app)
    response = client.post("/model-providers", json={
        "name": f"No Key {uuid.uuid4()}",
        "base_url": "https://provider.example/v1",
        "enabled": True,
    })

    assert response.status_code == 400
    assert response.json()["detail"] == "API key is required to enable this provider."


def test_model_provider_upsert_disabled_without_key_does_not_sync(monkeypatch):
    monkeypatch.setattr("app.routers.model_providers.key_vault_uri", lambda: "")

    client = TestClient(app)
    provider = _create_provider(client, f"No Key {uuid.uuid4()}", enabled=False)

    assert provider["api_key_status"] == "vault_not_configured"
    assert provider["models"] == []


def test_model_provider_sync_failure_is_rejected_without_saving_secret(monkeypatch):
    stored = _patch_key_vault(monkeypatch)

    async def fake_fetch_available_models(base_url: str, api_key: str | None = None):
        raise RuntimeError("provider rejected model discovery")

    monkeypatch.setattr("app.routers.model_providers._fetch_available_models", fake_fetch_available_models)

    client = TestClient(app)
    provider_name = f"Bad Sync {uuid.uuid4()}"
    response = client.post("/model-providers", json={
        "name": provider_name,
        "base_url": "https://provider.example/v1",
        "api_key": "secret-value",
    })

    assert response.status_code == 400
    assert "Provider model sync failed" in response.json()["detail"]
    assert stored == {}
    list_response = client.get("/model-providers")
    assert all(provider["name"] != provider_name for provider in list_response.json()["providers"])


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
    provider_id = str(uuid.uuid4())

    assert not any(getattr(route, "path", "") == "/model-providers/discover" for route in app.routes)

    discovery_response = client.post("/model-providers/discover", json={"provider_id": provider_id})
    manual_model_response = client.post(f"/model-providers/{provider_id}/models", json={
        "model_name": "manual-model",
    })

    assert discovery_response.status_code in {404, 405}
    assert manual_model_response.status_code == 404


def test_model_provider_route_selects_primary_model(monkeypatch):
    stored = _patch_key_vault(monkeypatch)

    async def fake_fetch_available_models(base_url: str, api_key: str | None = None):
        from app.routers.model_providers import DiscoveredModel

        assert api_key == "secret-value"
        return [DiscoveredModel(id="primary-model", display_name="Primary Model")]

    monkeypatch.setattr("app.routers.model_providers._fetch_available_models", fake_fetch_available_models)

    client = TestClient(app)
    suffix = str(uuid.uuid4())
    first_response = client.post("/model-providers", json={
        "name": f"Primary {suffix}",
        "base_url": "https://primary.example/v1",
        "api_key": "secret-value",
    })
    assert first_response.status_code == 201
    assert stored

    first_provider = next(item for item in first_response.json()["providers"] if item["name"] == f"Primary {suffix}")
    first_model = first_provider["models"][0]

    response = client.patch("/model-providers/route", json={
        "primary_model_id": first_model["id"],
    })

    assert response.status_code == 200
    route = response.json()["route"]
    assert route["primary_model_id"] == first_model["id"]


def test_model_provider_delete_removes_models_and_reconciles_route(monkeypatch):
    stored = _patch_key_vault(monkeypatch)

    async def fake_fetch_available_models(base_url: str, api_key: str | None = None):
        from app.routers.model_providers import DiscoveredModel

        assert api_key == "secret-value"
        if "primary" in base_url:
            return [DiscoveredModel(id="primary-model", display_name="Primary Model")]
        return [DiscoveredModel(id="replacement-model", display_name="Replacement Model")]

    monkeypatch.setattr("app.routers.model_providers._fetch_available_models", fake_fetch_available_models)

    client = TestClient(app)
    suffix = str(uuid.uuid4())
    first_response = client.post("/model-providers", json={
        "name": f"Delete Primary {suffix}",
        "base_url": "https://primary.example/v1",
        "api_key": "secret-value",
    })
    second_response = client.post("/model-providers", json={
        "name": f"Delete Replacement {suffix}",
        "base_url": "https://replacement.example/v1",
        "api_key": "secret-value",
    })
    assert first_response.status_code == 201
    assert second_response.status_code == 201
    assert len(stored) == 2

    first_provider = next(item for item in second_response.json()["providers"] if item["name"] == f"Delete Primary {suffix}")
    second_provider = next(item for item in second_response.json()["providers"] if item["name"] == f"Delete Replacement {suffix}")
    first_model = first_provider["models"][0]
    second_model = second_provider["models"][0]
    route_response = client.patch("/model-providers/route", json={
        "primary_model_id": first_model["id"],
    })
    assert route_response.status_code == 200
    extra_route_task = f"extra-delete-route-{suffix}"
    _add_route(extra_route_task, first_model["id"])

    delete_first_response = client.delete(f"/model-providers/{first_provider['id']}")
    assert delete_first_response.status_code == 200
    payload = delete_first_response.json()
    assert all(provider["id"] != first_provider["id"] for provider in payload["providers"])
    assert all(model["id"] != first_model["id"] for provider in payload["providers"] for model in provider["models"])
    assert payload["route"]["primary_model_id"] != first_model["id"]
    extra_route = _route_model_id(extra_route_task)
    assert extra_route is not None
    assert extra_route != first_model["id"]
    assert len(stored) == 1

    delete_second_response = client.delete(f"/model-providers/{second_provider['id']}")
    assert delete_second_response.status_code == 200
    payload = delete_second_response.json()
    assert all(provider["id"] != second_provider["id"] for provider in payload["providers"])
    route = payload["route"]
    if route:
        deleted_model_ids = {first_model["id"], second_model["id"]}
        assert route["primary_model_id"] not in deleted_model_ids
    extra_route = _route_model_id(extra_route_task)
    if extra_route:
        deleted_model_ids = {first_model["id"], second_model["id"]}
        assert extra_route not in deleted_model_ids
    assert stored == {}


def test_model_provider_delete_is_idempotent_for_stale_clients(monkeypatch):
    _patch_key_vault(monkeypatch)
    client = TestClient(app)
    provider = _create_provider(client, f"Stale Delete {uuid.uuid4()}", enabled=False)

    first_delete = client.delete(f"/model-providers/{provider['id']}")
    assert first_delete.status_code == 200
    assert all(item["id"] != provider["id"] for item in first_delete.json()["providers"])

    second_delete = client.delete(f"/model-providers/{provider['id']}")
    assert second_delete.status_code == 200
    assert all(item["id"] != provider["id"] for item in second_delete.json()["providers"])


def test_model_provider_delete_clears_traces_before_removing_last_route(monkeypatch):
    _patch_key_vault(monkeypatch)

    async def fake_fetch_available_models(base_url: str, api_key: str | None = None):
        from app.routers.model_providers import DiscoveredModel

        assert api_key == "secret-value"
        return [DiscoveredModel(id="provider-chat-model", display_name="Provider Chat Model")]

    monkeypatch.setattr("app.routers.model_providers._fetch_available_models", fake_fetch_available_models)

    client = TestClient(app)
    provider_name = f"Trace Delete {uuid.uuid4()}"
    response = client.post("/model-providers", json={
        "name": provider_name,
        "base_url": "https://trace-delete.example/v1",
        "api_key": "secret-value",
    })
    assert response.status_code == 201
    provider = next(item for item in response.json()["providers"] if item["name"] == provider_name)
    model = provider["models"][0]
    route_response = client.patch("/model-providers/route", json={
        "primary_model_id": model["id"],
    })
    assert route_response.status_code == 200
    _disable_other_providers(provider["id"])

    chat_route_id = _route_id("general_chat")
    assert chat_route_id is not None
    trace_id = _add_trace_for_route(chat_route_id)

    delete_response = client.delete(f"/model-providers/{provider['id']}")

    assert delete_response.status_code == 200
    assert _trace_route_id(trace_id) is None


def test_route_reconcile_flushes_before_bulk_model_delete():
    import asyncio

    from app.routers.model_providers import OPENAI_COMPATIBLE, _reconcile_routes_before_model_delete

    async def run_delete_flow() -> None:
        async with TestingSessionLocal() as session:
            await session.execute(text("PRAGMA foreign_keys=ON"))
            suffix = uuid.uuid4()
            old_provider = AIProvider(
                id=uuid.uuid4(),
                name=f"Flush Delete Old {suffix}",
                provider_type=OPENAI_COMPATIBLE,
                base_url="https://old-provider.example/v1",
                enabled="true",
            )
            new_provider = AIProvider(
                id=uuid.uuid4(),
                name=f"Flush Delete New {suffix}",
                provider_type=OPENAI_COMPATIBLE,
                base_url="https://new-provider.example/v1",
                enabled="true",
            )
            session.add_all([old_provider, new_provider])
            await session.flush()

            old_model = AIModel(
                id=uuid.uuid4(),
                provider_id=old_provider.id,
                display_name="Old Model",
                model_name="old-model",
                deployment_name="old-model",
                enabled="true",
            )
            new_model = AIModel(
                id=uuid.uuid4(),
                provider_id=new_provider.id,
                display_name="New Model",
                model_name="new-model",
                deployment_name="new-model",
                enabled="true",
            )
            session.add_all([old_model, new_model])
            await session.flush()

            task_type = f"flush-delete-{suffix}"
            session.add(AIRoute(
                id=uuid.uuid4(),
                task_type=task_type,
                primary_model_id=old_model.id,
                temperature=Decimal("0.30"),
                max_tokens=2000,
                enabled="true",
            ))
            await session.commit()

            await _reconcile_routes_before_model_delete(session, {old_model.id})
            await session.execute(delete(AIModel).where(AIModel.provider_id == old_provider.id))
            await session.commit()

            route_result = await session.execute(select(AIRoute).where(AIRoute.task_type == task_type))
            route = route_result.scalar_one()
            assert route.primary_model_id == new_model.id

    asyncio.run(run_delete_flow())


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
    model_providers._require_admin({"roles": ["AIPlatform.User"], "mode": "api-key"})
