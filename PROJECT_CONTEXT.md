# Project Context

This is the Lots Lots More custom AI Platform.

Core repo: `aldenbronkhorst/ai-platform`

Main components:
- `apps/ai-core-api` — FastAPI backend / AI orchestration
- `apps/web-portal` — React frontend
- `apps/odoo-connector-api` — Odoo connector service
- `infra/bicep` — Azure infrastructure

The project is deployed on Azure and uses GitHub for source control/CI. The working agent may have access to Azure and GitHub resources, and should inspect those systems when needed instead of guessing.

Architecture direction:
- 3 agents only: Orchestrator, Memory, Reviewer
- Connectors like Odoo, Azure AI Search, documents, and currency handling are tools/services, not extra agents
- Business rules, company facts, and memories should be stored/configured in the platform, not hardcoded for every new business rule
