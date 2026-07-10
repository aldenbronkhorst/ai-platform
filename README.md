# AI Platform

## Local Full-Stack Testing

Run the full platform locally, while using the same Azure-backed database, Key Vault secrets, Microsoft login, and existing connector records:

```bash
./scripts/dev-local-stack.sh
```

Then open:

```text
http://localhost:5173
```

The script starts:

- web portal: `http://localhost:5173`
- AI core API: `http://127.0.0.1:8000`
- Odoo connector API from the sibling `ai-platform-connector-odoo` repo: `http://127.0.0.1:8010`

It pulls required secrets from Azure Container Apps at runtime and does not write production secrets into repo files. It also creates or updates a single PostgreSQL firewall rule for this Mac's current public IP so the local API can reach the live database. You must be logged into Azure CLI as an account with access to the production resource group:

```bash
az account show
```

Press `Ctrl+C` in the script terminal to stop all local services. Logs are written to `.local/logs/`.

Optional local overrides:

```bash
UVICORN_RELOAD=true ./scripts/dev-local-stack.sh
LOCAL_POSTGRES_FIREWALL=false ./scripts/dev-local-stack.sh
ODOO_CONNECTOR_DIR=/path/to/ai-platform-connector-odoo ./scripts/dev-local-stack.sh
```

Cloud-hosted AI workspace for Lots Lots More.

The product goal is simple: users sign in with Microsoft, connect their own
business accounts, and use chat to instruct the AI to work through those
connectors. It should feel closer to a cloud Codex-style operator than a set of
separate admin tools.

## Current Components

- `apps/web-portal` - React web app for chat, connector setup, and admin review.
- `apps/ai-core-api` - FastAPI orchestration API for auth, chat, memory, tools,
  chat file uploads, tracing, and connector routing.
- `infra/bicep` - Azure infrastructure for the deployed app.

Odoo connector source lives in the separate `aldenbronkhorst/ai-platform-connector-odoo`
repo. AI Platform consumes that service through `ODOO_CONNECTOR_URL`; it does not own
or build the connector implementation.

## Core Runtime

- Microsoft Entra sign-in for users.
- Per-user connector credentials stored in Azure Key Vault.
- Chat sessions and platform state stored in PostgreSQL.
- Uploaded chat files stored in Azure Blob Storage.
- Connectors store user-scoped access. Workspace is the model-facing execution
  surface and can call connected systems through the broker when needed.
- Memory extraction/review runs inside the API path for now.
- Direct OpenAI-compatible model providers are configured from the portal's
  AI Providers page. A provider stores the API endpoint and key; models are then
  fetched from the provider where supported and enabled individually for chat.
  API keys are written to Key Vault and are never returned to the browser after
  saving.

## Deliberately Not In Scope Right Now

- Separate task tracker product.
- Separate document vault product.
- Separate model marketplace or billing product.
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
pip install -r requirements-dev.txt
pytest
```

Portal build:

```bash
cd apps/web-portal
npm ci --workspaces=false
npm run build --workspaces=false
```
