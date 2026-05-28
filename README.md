# AI Platform

Azure-native AI Platform for Lots Lots More.

## Repository Structure

```
.
├── apps/
│   └── ai-core-api/          # FastAPI core API
├── infra/
│   ├── bicep/                # Infrastructure as Code (Bicep)
│   │   ├── main.bicep
│   │   └── modules/
│   └── scripts/              # Setup and utility scripts
├── runners/                  # Container app job runner images
├── packages/                 # Shared libraries
├── docs/                     # Documentation
├── schemas/                  # OpenAPI, DB, and event schemas
├── tests/                    # Integration and E2E tests
└── .github/workflows/        # GitHub Actions CI/CD
```

## Quick Start

### Prerequisites

- Azure CLI
- GitHub CLI (`gh`)
- Docker

### Setup

1. **Clone the repository**

2. **Run OIDC setup script** (one-time):
   ```bash
   bash infra/scripts/setup-github-oidc.sh "lots-ai-platform/ai-platform" dev
   ```
   Add the output secrets to your GitHub repository settings.

3. **Deploy infrastructure**:
   Infrastructure deploys automatically via GitHub Actions on push to `main`, or manually via workflow dispatch.

4. **Deploy API**:
   API builds and deploys automatically on changes to `apps/ai-core-api/`.

## Architecture

- **Azure API Management**: Public gateway
- **Azure Container Apps**: AI Core API runtime
- **Azure PostgreSQL Flexible Server**: Structured data
- **Azure Blob Storage**: Artifacts, OCR, reports
- **Azure Key Vault**: Secrets and credentials
- **Azure Service Bus**: Work queues
- **Azure Durable Functions**: Workflows and automations
- **Azure AI Search**: Knowledge retrieval
- **Application Insights**: Monitoring and logging

## Environments

| Environment | Resource Group | Status |
|-------------|---------------|--------|
| dev | `rg-aiplatform-dev` | Active |
| prod | `rg-aiplatform-prod` | Planned |

## Tags

All Azure resources are tagged with:

- `project=ai-platform`
- `environment=dev|prod`
- `owner=alden`
- `managed-by=iac`
- `cost-center=ai-platform`
