"""Tests for memory CRUD endpoints, MemoryCandidateService, and chat integration."""
import os
import uuid
import pytest
from datetime import datetime, timezone

os.environ["DEBUG"] = "true"
os.environ["ODOO_CONNECTOR_URL"] = "http://mock-connector:8000"
os.environ["ODOO_CONNECTOR_API_KEY"] = "test-key"

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


# ── MemoryCandidateService unit tests ──

class TestMemoryCandidateService:
    @pytest.mark.asyncio
    async def test_explicit_remember_this(self, mock_db):
        from app.services.memory import MemoryCandidateService
        from app.models.models import AIChatMessage

        msg = AIChatMessage(
            id=uuid.uuid4(),
            chat_session_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            role="user",
            content="Remember this: ABC customers need special attention",
            created_at=datetime.now(timezone.utc),
        )
        svc = MemoryCandidateService(mock_db)
        candidates = await svc.extract_from_messages([msg], user_id=uuid.uuid4())
        assert len(candidates) == 1
        c = candidates[0]
        assert c.type == "system_behavior"
        assert c.risk_level == "medium"
        assert c.save_mode == "confirm"

    @pytest.mark.asyncio
    async def test_correction_detected(self, mock_db):
        from app.services.memory import MemoryCandidateService

        msg = AIChatMessage(
            id=uuid.uuid4(),
            chat_session_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            role="user",
            content="No, that's not dollars, it should be ZAR",
            created_at=datetime.now(timezone.utc),
        )
        svc = MemoryCandidateService(mock_db)
        candidates = await svc.extract_from_messages([msg], user_id=uuid.uuid4())
        assert len(candidates) == 1
        c = candidates[0]
        assert c.type == "correction"
        assert c.risk_level == "medium"

    @pytest.mark.asyncio
    async def test_resolved_case_detected(self, mock_db):
        from app.services.memory import MemoryCandidateService

        msg = AIChatMessage(
            id=uuid.uuid4(),
            chat_session_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            role="user",
            content="Thanks, that worked! The downstairs printer setup is working now.",
            created_at=datetime.now(timezone.utc),
        )
        svc = MemoryCandidateService(mock_db)
        candidates = await svc.extract_from_messages([msg], user_id=uuid.uuid4())
        assert len(candidates) >= 1
        c = candidates[0]
        assert c.type == "resolved_case"
        assert c.risk_level == "low"
        assert c.save_mode == "auto"

    @pytest.mark.asyncio
    async def test_no_candidate_for_normal_chat(self, mock_db):
        from app.services.memory import MemoryCandidateService

        msg = AIChatMessage(
            id=uuid.uuid4(),
            chat_session_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            role="user",
            content="What is the weather today?",
            created_at=datetime.now(timezone.utc),
        )
        svc = MemoryCandidateService(mock_db)
        candidates = await svc.extract_from_messages([msg], user_id=uuid.uuid4())
        assert len(candidates) == 0

    @pytest.mark.asyncio
    async def test_duplicate_check(self, mock_db):
        from app.services.memory import MemoryCandidateService
        from app.schemas.schemas import MemoryCandidate

        existing = AIMemory(
            id=uuid.uuid4(),
            type="system_behavior",
            title="Test memory that already exists",
            body="This is a test",
            status="active",
            confidence="medium",
            risk_level="low",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        mock_db.memories.append(existing)

        candidate = MemoryCandidate(
            type="system_behavior",
            title="Test memory that already exists",
            body="This is a test",
            confidence="medium",
            risk_level="low",
            save_mode="confirm",
        )
        svc = MemoryCandidateService(mock_db)
        is_dup = await svc.check_duplicate(candidate)
        assert is_dup is True

    @pytest.mark.asyncio
    async def test_save_candidate_auto_active(self, mock_db):
        from app.services.memory import MemoryCandidateService
        from app.schemas.schemas import MemoryCandidate

        candidate = MemoryCandidate(
            type="resolved_case",
            title="Test auto-save",
            body="This should be active",
            confidence="high",
            risk_level="low",
            save_mode="auto",
        )
        svc = MemoryCandidateService(mock_db)
        memory = await svc.save_candidate(candidate, user_id=uuid.uuid4())
        assert memory.status == "active"
        assert memory.type == "resolved_case"

    @pytest.mark.asyncio
    async def test_save_candidate_draft(self, mock_db):
        from app.services.memory import MemoryCandidateService
        from app.schemas.schemas import MemoryCandidate

        candidate = MemoryCandidate(
            type="system_behavior",
            title="Test draft save",
            body="This needs confirmation",
            confidence="medium",
            risk_level="medium",
            save_mode="confirm",
        )
        svc = MemoryCandidateService(mock_db)
        memory = await svc.save_candidate(candidate, user_id=uuid.uuid4())
        assert memory.status == "draft"
        assert memory.type == "system_behavior"


# ── Memory endpoint tests ──

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
