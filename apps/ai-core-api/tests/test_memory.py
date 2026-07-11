"""The removed automatic memory API remains unavailable."""
import os
import uuid
import pytest
from datetime import datetime, timezone

os.environ["DEBUG"] = "true"
os.environ["CONNECTOR_ENDPOINTS_JSON"] = '{"odoo":{"base_url":"http://mock-connector:8000"}}'
os.environ["CONNECTOR_INTERNAL_API_KEY"] = "test-key"

from app.main import app
from app.core.database import get_db
from app.models.models import AIMemory, AIChatMessage


class MockSession:
    def __init__(self):
        self.added = []
        self.flushed = False
        self.memories: list[AIMemory] = []
        self.messages: list[AIChatMessage] = []
        self.committed = False

    async def execute(self, stmt, *args, **kwargs):
        stmt_str = str(stmt).lower()

        class MockResult:
            def __init__(self, data):
                self._data = data

            def scalar_one_or_none(self):
                return self._data[0] if self._data else None

            def scalars(self):
                return self

            def all(self):
                return self._data

            def first(self):
                return self._data[0] if self._data else None

        # Simulate select queries via table name matching
        if "ai_memories" in stmt_str:
            return MockResult(self.memories)
        if "ai_chat_messages" in stmt_str:
            return MockResult(self.messages)
        if "ai_chat_sessions" in stmt_str:
            return MockResult([])

        return MockResult([])

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.flushed = True
        for obj in self.added:
            if isinstance(obj, AIMemory):
                if not obj.id:
                    obj.id = uuid.uuid4()
                obj.created_at = datetime.now(timezone.utc)
                obj.updated_at = datetime.now(timezone.utc)
                self.memories.append(obj)

    async def commit(self):
        self.committed = True
        if not self.flushed:
            await self.flush()

    async def refresh(self, obj):
        pass

    def add_all(self, objs):
        self.added.extend(objs)


@pytest.fixture
def mock_db():
    return MockSession()


@pytest.fixture
def client(mock_db):
    async def override_get_db():
        yield mock_db
    app.dependency_overrides[get_db] = override_get_db
    from fastapi.testclient import TestClient
    test_client = TestClient(app)
    yield test_client
    app.dependency_overrides.clear()


def seed_memory(
    mock_db: MockSession,
    *,
    type: str = "general_note",
    title: str = "Seeded memory",
    body: str | None = None,
    status: str = "draft",
    risk_level: str = "low",
    created_by_user_id: uuid.UUID | None = None,
) -> AIMemory:
    now = datetime.now(timezone.utc)
    memory = AIMemory(
        id=uuid.uuid4(),
        type=type,
        title=title,
        body=body,
        status=status,
        confidence="medium",
        risk_level=risk_level,
        priority=100,
        success_count=0,
        failure_count=0,
        version=1,
        created_by_user_id=created_by_user_id,
        created_at=now,
        updated_at=now,
    )
    mock_db.memories.append(memory)
    return memory


class TestMemoryEndpoints:
    def test_list_memories_is_not_public_api(self, client):
        res = client.get("/memories")
        assert res.status_code == 404

    def test_create_memory_is_not_public_api(self, client):
        res = client.post("/memories", json={
            "type": "general_note",
            "title": "Test memory",
            "body": "This is a test memory",
            "risk_level": "low",
            "status": "draft",
        })
        assert res.status_code == 404

    def test_update_memory_is_not_public_api(self, client):
        res = client.patch(f"/memories/{uuid.uuid4()}", json={
            "title": "Updated title",
            "body": "Updated body",
        })
        assert res.status_code == 404

    def test_get_memory_is_not_public_api(self, client):
        res = client.get(f"/memories/{uuid.uuid4()}")
        assert res.status_code == 404

    def test_filter_memories_is_not_public_api(self, client):
        res = client.get("/memories?type=procedure")
        assert res.status_code == 404
