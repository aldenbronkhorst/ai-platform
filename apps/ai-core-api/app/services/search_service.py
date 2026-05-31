import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from uuid import UUID

from azure.identity import DefaultAzureCredential
from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    SearchIndex,
    SimpleField,
    SearchableField,
    SearchFieldDataType,
)

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class SearchService:
    def __init__(self, index_name: Optional[str] = None):
        settings = get_settings()
        self.enabled = settings.azure_search_enable
        self.endpoint = settings.azure_search_endpoint
        self.index_name = index_name or settings.azure_search_index_name
        self.max_results = settings.azure_search_max_results

        self._index_client: Optional[SearchIndexClient] = None
        self._search_client: Optional[SearchClient] = None

    def _get_credential(self) -> Any:
        """Prefers Managed Identity, can fall back to local credentials/DefaultAzureCredential."""
        settings = get_settings()
        if settings.azure_client_id:
            return DefaultAzureCredential(managed_identity_client_id=settings.azure_client_id)
        return DefaultAzureCredential()

    def _get_index_client(self) -> Optional[SearchIndexClient]:
        if not self.enabled or not self.endpoint:
            return None
        if not self._index_client:
            try:
                self._index_client = SearchIndexClient(
                    endpoint=self.endpoint,
                    credential=self._get_credential()
                )
            except Exception as e:
                logger.error("Failed to initialize SearchIndexClient: %s", e)
        return self._index_client

    def _get_search_client(self) -> Optional[SearchClient]:
        if not self.enabled or not self.endpoint:
            return None
        if not self._search_client:
            try:
                self._search_client = SearchClient(
                    endpoint=self.endpoint,
                    index_name=self.index_name,
                    credential=self._get_credential()
                )
            except Exception as e:
                logger.error("Failed to initialize SearchClient: %s", e)
        return self._search_client

    async def create_or_update_index_idempotent(self) -> bool:
        """Idempotently creates or updates the search index with the required schema."""
        client = self._get_index_client()
        if not client:
            logger.warning("Azure AI Search is disabled or not configured. Skipping index creation.")
            return False

        fields = [
            SimpleField(name="id", type=SearchFieldDataType.String, key=True),
            SimpleField(name="source_type", type=SearchFieldDataType.String, filterable=True, facetable=True),
            SimpleField(name="source_id", type=SearchFieldDataType.String, filterable=True),
            SimpleField(name="memory_id", type=SearchFieldDataType.String, filterable=True),
            SimpleField(name="document_id", type=SearchFieldDataType.String, filterable=True),
            SearchableField(name="title", type=SearchFieldDataType.String, analyzer_name="en.microsoft"),
            SearchableField(name="chunk_text", type=SearchFieldDataType.String, analyzer_name="en.microsoft"),
            SearchableField(name="summary", type=SearchFieldDataType.String, analyzer_name="en.microsoft"),
            SimpleField(name="type", type=SearchFieldDataType.String, filterable=True, facetable=True),
            SimpleField(name="scope_type", type=SearchFieldDataType.String, filterable=True, facetable=True),
            SimpleField(name="scope_value", type=SearchFieldDataType.String, filterable=True),
            SimpleField(name="department", type=SearchFieldDataType.String, filterable=True, facetable=True),
            SimpleField(name="customer", type=SearchFieldDataType.String, filterable=True, facetable=True),
            SimpleField(name="supplier", type=SearchFieldDataType.String, filterable=True, facetable=True),
            SimpleField(name="status", type=SearchFieldDataType.String, filterable=True, facetable=True),
            SimpleField(name="risk_level", type=SearchFieldDataType.String, filterable=True, facetable=True),
            SimpleField(name="confidence", type=SearchFieldDataType.String, filterable=True, facetable=True),
            SimpleField(name="tags", type=SearchFieldDataType.Collection(SearchFieldDataType.String), filterable=True, facetable=True),
            SimpleField(name="created_at", type=SearchFieldDataType.DateTimeOffset, filterable=True),
            SimpleField(name="updated_at", type=SearchFieldDataType.DateTimeOffset, filterable=True),
            SimpleField(name="last_confirmed_at", type=SearchFieldDataType.DateTimeOffset, filterable=True),
            SimpleField(name="visibility", type=SearchFieldDataType.String, filterable=True, facetable=True),
            SimpleField(name="created_by_user_id", type=SearchFieldDataType.String, filterable=True),
        ]

        index = SearchIndex(name=self.index_name, fields=fields)

        try:
            logger.info("Idempotently creating/updating index '%s'...", self.index_name)
            client.create_or_update_index(index)
            logger.info("Successfully created/updated search index '%s'", self.index_name)
            return True
        except Exception as e:
            logger.error("Failed to create/update search index '%s': %s", self.index_name, e)
            return False

    async def index_documents(self, documents: List[Dict[str, Any]]) -> bool:
        """Uploads or merges a list of document dicts into the index."""
        client = self._get_search_client()
        if not client:
            return False
        if not documents:
            return True

        # Sanitize/format documents to ensure compatibility
        sanitized_docs = []
        for doc in documents:
            s_doc = dict(doc)
            # Ensure DateTime values are converted to ISO 8601 strings with timezone
            for key, val in list(s_doc.items()):
                if isinstance(val, datetime):
                    if val.tzinfo is None:
                        val = val.replace(tzinfo=timezone.utc)
                    s_doc[key] = val.isoformat()
                elif isinstance(val, UUID):
                    s_doc[key] = str(val)
            sanitized_docs.append(s_doc)

        try:
            logger.info("Uploading %d documents to index '%s'...", len(sanitized_docs), self.index_name)
            results = client.upload_documents(documents=sanitized_docs)
            success_count = sum(1 for r in results if r.succeeded)
            logger.info("Successfully uploaded %d/%d documents to index '%s'", success_count, len(sanitized_docs), self.index_name)
            return success_count == len(sanitized_docs)
        except Exception as e:
            logger.error("Failed to upload documents to index '%s': %s", self.index_name, e)
            return False

    async def delete_documents(self, document_ids: List[str]) -> bool:
        """Deletes a list of documents from the search index by their IDs."""
        client = self._get_search_client()
        if not client:
            return False
        if not document_ids:
            return True

        payload = [{"id": doc_id} for doc_id in document_ids]
        try:
            logger.info("Deleting %d documents from index '%s'...", len(document_ids), self.index_name)
            results = client.delete_documents(documents=payload)
            success_count = sum(1 for r in results if r.succeeded)
            logger.info("Successfully deleted %d/%d documents from index '%s'", success_count, len(document_ids), self.index_name)
            return success_count == len(document_ids)
        except Exception as e:
            logger.error("Failed to delete documents from index '%s': %s", self.index_name, e)
            return False

    async def search_memories(
        self,
        query: str,
        user_id: Optional[UUID] = None,
        scope_type: Optional[str] = None,
        scope_value: Optional[str] = None,
        risk_levels: Optional[List[str]] = None,
        types: Optional[List[str]] = None,
        status: Optional[str] = "active",
        limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Search for company memories/SOPs/resolved cases with semantic/keyword matching and strict security filtering."""
        client = self._get_search_client()
        if not client:
            return []

        # Build OData filter string to enforce security boundaries and scopes
        filter_parts = []

        # 1. Enforce active/approved memories only
        if status:
            filter_parts.append(f"status eq '{status}'")

        # 2. Scope & Permission Gating
        # Normal user can only see 'global' memories OR their own 'user'-scoped memories.
        # Admin can see company/global memories.
        if user_id:
            user_filter = f"(visibility eq 'global' or created_by_user_id eq '{user_id}')"
            filter_parts.append(user_filter)
        else:
            filter_parts.append("visibility eq 'global'")

        if scope_type:
            filter_parts.append(f"scope_type eq '{scope_type}'")
        if scope_value:
            filter_parts.append(f"scope_value eq '{scope_value}'")

        # 3. Content Type filtering
        if types:
            type_filters = " or ".join(f"type eq '{t}'" for t in types)
            filter_parts.append(f"({type_filters})")

        # 4. Risk level boundaries
        if risk_levels:
            risk_filters = " or ".join(f"risk_level eq '{r}'" for r in risk_levels)
            filter_parts.append(f"({risk_filters})")

        filter_string = " and ".join(filter_parts) if filter_parts else None
        search_limit = limit or self.max_results

        logger.info(
            "Searching index '%s' | query='%s' limit=%d filter='%s'",
            self.index_name, query, search_limit, filter_string
        )

        try:
            results = client.search(
                search_text=query,
                filter=filter_string,
                top=search_limit,
                select=[
                    "id", "source_type", "source_id", "memory_id", "document_id",
                    "title", "chunk_text", "summary", "type", "scope_type",
                    "scope_value", "department", "customer", "supplier",
                    "status", "risk_level", "confidence", "tags"
                ]
            )

            hits = []
            for hit in results:
                # Extract clean dict and include search score
                doc = {key: hit[key] for key in hit.keys() if key != "@search.score"}
                doc["score"] = hit.get("@search.score", 0.0)
                hits.append(doc)

            logger.info("Search complete | index='%s' results_count=%d", self.index_name, len(hits))
            return hits
        except Exception as e:
            logger.error("Failed to search index '%s': %s", self.index_name, e)
            return []

    async def index_memory_record(self, memory: Any) -> bool:
        """Helper to index a single AIMemory model record into Azure AI Search."""
        if not self.enabled or not self.endpoint:
            return False

        doc = {
            "id": str(memory.id),
            "source_type": "ai_memory",
            "source_id": str(memory.id),
            "memory_id": str(memory.id),
            "document_id": None,
            "title": memory.title,
            "chunk_text": memory.body or "",
            "summary": memory.summary or "",
            "type": memory.type,
            "scope_type": memory.scope_type,
            "scope_value": memory.scope_value,
            "department": getattr(memory, "department", None),
            "customer": getattr(memory, "customer", None),
            "supplier": getattr(memory, "supplier", None),
            "status": memory.status,
            "risk_level": memory.risk_level,
            "confidence": memory.confidence,
            "tags": getattr(memory, "tags", None) or [],
            "created_at": memory.created_at,
            "updated_at": memory.updated_at,
            "last_confirmed_at": getattr(memory, "last_confirmed_at", None),
            "visibility": "global" if memory.scope_type == "global" else "user",
            "created_by_user_id": str(memory.created_by_user_id) if memory.created_by_user_id else None,
        }
        return await self.index_documents([doc])

    async def delete_memory_record(self, memory_id: Any) -> bool:
        """Helper to delete a single memory record from Azure AI Search index."""
        if not self.enabled or not self.endpoint:
            return False
        return await self.delete_documents([str(memory_id)])
