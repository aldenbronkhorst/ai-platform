# Project Context

This is the Lots Lots More custom AI Platform.

Core repo: `aldenbronkhorst/ai-platform`

Current production shape:
- `apps/web-portal` - React frontend for chat, connector setup, and admin review.
- `apps/ai-core-api` - FastAPI backend and AI orchestration layer.
- `apps/odoo-connector-api` - internal Odoo connector service.
- `infra/bicep` - Azure infrastructure.

Architecture direction:
- Keep the main workflow chat-first.
- Keep connectors as tools, not separate products.
- Keep AI provider setup in the portal, with providers entered by the user and models discovered from the provider API.
- Keep memory and review support, but avoid a standalone task tracker until it is needed.
- Do not add queue workers, durable workflows, or search infrastructure without a concrete workflow that needs them.

When inspecting deployment state, prefer Azure/GitHub reality over stale docs.
