# AI Platform

Cloud-hosted AI workspace for Lots Lots More.

The product goal is simple: users sign in with Microsoft, connect their own
business accounts, and use chat to instruct the AI to work through those
connectors. It should feel closer to a cloud Codex-style operator than a set of
separate admin tools.

## Current Components

- `apps/web-portal` - React web app for chat, connector setup, and admin review.
- `apps/ai-core-api` - FastAPI orchestration API for auth, chat, memory, tools,
  chat file uploads, tracing, and connector routing.
- `apps/odoo-connector-api` - Internal Odoo execution service.
- `infra/bicep` - Azure infrastructure for the deployed app.

## Core Runtime

- Microsoft Entra sign-in for users.
- Per-user connector credentials stored in Azure Key Vault.
- Chat sessions and platform state stored in PostgreSQL.
- Uploaded chat files stored in Azure Blob Storage.
- Odoo, GitHub, and Microsoft admin connectors exposed as model tools.
- Memory extraction/review runs inside the API path for now.
- Direct model providers: Kimi is the primary chat model provider and DeepSeek
  is the fallback provider. Production expects Key Vault secrets named
  `model-provider-kimi-api-key` and `model-provider-deepseek-api-key`, or local
  environment variables `KIMI_API_KEY`/`MOONSHOT_API_KEY` and `DEEPSEEK_API_KEY`.
- Admins can manage OpenAI-compatible model providers from the portal's
  AI Providers page. API keys are written to Key Vault and are never returned to
  the browser after saving.

## Deliberately Not In Scope Right Now

- Separate task tracker product.
- Separate document vault product.
- Editable AI model/provider configuration UI.
- Service Bus worker queues.
- Durable Functions automation workflows.
- Azure AI Search knowledge indexing.

Those can come back when there is a concrete workflow that justifies the extra
runtime and operational surface.

## Development

```bash
npm --workspace apps/web-portal run dev
```

Backend tests:

```bash
cd apps/ai-core-api
pytest
```

Portal build:

```bash
cd apps/web-portal
npm ci --workspaces=false
npm run build --workspaces=false
```
