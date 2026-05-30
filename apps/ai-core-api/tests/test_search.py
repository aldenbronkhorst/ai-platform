import os
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from uuid import UUID, uuid4

from app.core.config import get_settings
from app.services.search_service import SearchService


class TestSearchServiceConfig:
    def test_search_config_loading_default(self):
        settings = get_settings()
        # Verify default types are correct
        assert isinstance(settings.azure_search_enable, bool)
        assert isinstance(settings.azure_search_index_name, str)
        assert isinstance(settings.azure_search_max_results, int)

    @patch.dict(os.environ, {
        "AZURE_SEARCH_ENDPOINT": "https://test-search.search.windows.net",
        "AZURE_SEARCH_INDEX_NAME": "test-knowledge",
        "AZURE_SEARCH_ENABLE": "true",
        "AZURE_SEARCH_MAX_RESULTS": "10",
        "AZURE_SEARCH_MAX_INJECTED_CHUNKS": "3"
    })
    def test_search_config_overrides(self):
        get_settings.cache_clear()
        settings = get_settings()
        assert settings.azure_search_endpoint == "https://test-search.search.windows.net"
        assert settings.azure_search_index_name == "test-knowledge"
        assert settings.azure_search_enable is True
        assert settings.azure_search_max_results == 10
        assert settings.azure_search_max_injected_chunks == 3
        get_settings.cache_clear()


class TestSearchServiceLogic:
    @pytest.fixture(autouse=True)
    def setup_env(self):
        with patch.dict(os.environ, {
            "AZURE_SEARCH_ENDPOINT": "https://test-search.search.windows.net",
            "AZURE_SEARCH_INDEX_NAME": "test-knowledge",
            "AZURE_SEARCH_ENABLE": "true"
        }):
            get_settings.cache_clear()
            yield
            get_settings.cache_clear()

    @pytest.mark.asyncio
    @patch("app.services.search_service.SearchIndexClient")
    async def test_create_or_update_index_idempotent_success(self, mock_index_client_cls):
        mock_client = MagicMock()
        mock_index_client_cls.return_value = mock_client

        svc = SearchService()
        success = await svc.create_or_update_index_idempotent()
        assert success is True
        mock_client.create_or_update_index.assert_called_once()

    @pytest.mark.asyncio
    @patch("app.services.search_service.SearchIndexClient")
    async def test_create_or_update_index_idempotent_failure(self, mock_index_client_cls):
        mock_client = MagicMock()
        mock_client.create_or_update_index.side_effect = Exception("Azure API Error")
        mock_index_client_cls.return_value = mock_client

        svc = SearchService()
        success = await svc.create_or_update_index_idempotent()
        assert success is False

    @pytest.mark.asyncio
    @patch("app.services.search_service.SearchClient")
    async def test_index_documents_payload(self, mock_search_client_cls):
        mock_client = MagicMock()
        mock_results = [MagicMock(succeeded=True), MagicMock(succeeded=True)]
        mock_client.upload_documents.return_value = mock_results
        mock_search_client_cls.return_value = mock_client

        svc = SearchService()
        docs = [
            {
                "id": "1",
                "title": "SOP downstairs printer",
                "chunk_text": "Select tray 2",
                "type": "procedure"
            },
            {
                "id": "2",
                "title": "Resolved case printer",
                "chunk_text": "Printer works",
                "type": "resolved_case"
            }
        ]
        success = await svc.index_documents(docs)
        assert success is True
        mock_client.upload_documents.assert_called_once_with(documents=docs)

    @pytest.mark.asyncio
    @patch("app.services.search_service.SearchClient")
    async def test_delete_documents(self, mock_search_client_cls):
        mock_client = MagicMock()
        mock_results = [MagicMock(succeeded=True)]
        mock_client.delete_documents.return_value = mock_results
        mock_search_client_cls.return_value = mock_client

        svc = SearchService()
        success = await svc.delete_documents(["1"])
        assert success is True
        mock_client.delete_documents.assert_called_once_with(documents=[{"id": "1"}])

    @pytest.mark.asyncio
    @patch("app.services.search_service.SearchClient")
    async def test_search_memories_filters_and_security(self, mock_search_client_cls):
        mock_client = MagicMock()
        mock_search_client_cls.return_value = mock_client

        # Mock search results returning hits with search score
        mock_hit1 = {"id": "1", "title": "SOP", "chunk_text": "some text", "type": "procedure"}
        mock_hit2 = {"id": "2", "title": "SOP 2", "chunk_text": "other text", "type": "procedure"}
        mock_results = MagicMock()
        mock_results.__iter__.return_value = [
            MagicMock(keys=lambda: mock_hit1.keys(), get=lambda k, d=None: mock_hit1.get(k, d) if k != "@search.score" else 1.0, __getitem__=lambda s, k: mock_hit1[k]),
            MagicMock(keys=lambda: mock_hit2.keys(), get=lambda k, d=None: mock_hit2.get(k, d) if k != "@search.score" else 0.8, __getitem__=lambda s, k: mock_hit2[k])
        ]
        mock_client.search.return_value = mock_results

        svc = SearchService()
        user_uuid = uuid4()
        hits = await svc.search_memories(
            query="printer",
            user_id=user_uuid,
            scope_type="global",
            status="active"
        )

        assert len(hits) == 2
        mock_client.search.assert_called_once()
        call_kwargs = mock_client.search.call_args[1]
        assert call_kwargs["search_text"] == "printer"
        # Verify the security filter gates status and user scoped visibility
        assert f"status eq 'active'" in call_kwargs["filter"]
        assert f"created_by_user_id eq '{user_uuid}'" in call_kwargs["filter"]
        assert "scope_type eq 'global'" in call_kwargs["filter"]

    @pytest.mark.asyncio
    @patch("app.services.search_service.SearchClient")
    async def test_search_disabled_returns_empty(self, mock_search_client_cls):
        # Set enable to false
        with patch.dict(os.environ, {"AZURE_SEARCH_ENABLE": "false"}):
            get_settings.cache_clear()
            svc = SearchService()
            assert svc.enabled is False
            hits = await svc.search_memories("test")
            assert hits == []
            mock_search_client_cls.assert_not_called()
