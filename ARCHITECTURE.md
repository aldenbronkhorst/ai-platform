# AI Platform Architecture

## Goal

A cloud-hosted AI operator for Lots Lots More. Users sign in with Microsoft,
connect business systems with their own accounts, and use chat to ask the AI to
read, reason, and act through those connectors.

## Keep

- Chat-first web portal.
- Microsoft Entra user auth.
- Per-user connector auth for Odoo, GitHub, and Microsoft admin workloads.
- Key Vault for connector tokens and secrets.
- PostgreSQL for chat, memory, model route seed data, traces, usage logs, and connector state.
- Blob Storage for uploaded chat attachments.
- Internal Odoo connector service.
- Application Insights/Log Analytics.
- Editable AI provider setup for OpenAI-compatible providers, with models discovered from the provider API.

## Cut For Now

- Standalone Tasks page/API.
- Standalone Documents vault page.
- Service Bus worker and unused queues.
- Durable workflow/automation runtime.
- Azure AI Search indexing.
- Not-implemented task graph/sub-agent scaffolding.

## Backlog

Bring these back only when the workflow is specific enough to justify the
runtime cost:

- Scheduled automations.
- Notification delivery.
- Search-backed company knowledge.
- Dedicated task inbox.
- Dedicated document library.
- Multi-agent task execution.
