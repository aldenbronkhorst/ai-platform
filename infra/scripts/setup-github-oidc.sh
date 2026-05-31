#!/bin/bash
set -e

# Setup script for GitHub OIDC federated credentials with Azure
# Run this locally after creating the GitHub repo

REPO_NAME="${1:-lots-ai-platform/ai-platform}"
ENVIRONMENT="${2:-prod}"
SUBSCRIPTION_ID=$(az account show --query id -o tsv)
TENANT_ID=$(az account show --query tenantId -o tsv)
APP_NAME="github-actions-ai-platform-${ENVIRONMENT}"

# Check if app registration exists
APP_ID=$(az ad app list --display-name "$APP_NAME" --query "[0].appId" -o tsv)

if [ -z "$APP_ID" ]; then
    echo "Creating app registration: $APP_NAME"
    APP_ID=$(az ad app create --display-name "$APP_NAME" --query appId -o tsv)
else
    echo "App registration already exists: $APP_ID"
fi

# Create service principal if not exists
SP_ID=$(az ad sp list --filter "appId eq '$APP_ID'" --query "[0].id" -o tsv)
if [ -z "$SP_ID" ]; then
    echo "Creating service principal"
    SP_ID=$(az ad sp create --id "$APP_ID" --query id -o tsv)
fi

# Get object ID of the app for federated credentials
APP_OBJECT_ID=$(az ad app show --id "$APP_ID" --query id -o tsv)

# Create federated credential for GitHub Actions (main branch)
FIC_NAME="github-actions-${ENVIRONMENT}"
FIC_EXISTS=$(az ad app federated-credential list --id "$APP_ID" --query "[?name=='$FIC_NAME'].name" -o tsv)

if [ -z "$FIC_EXISTS" ]; then
    echo "Creating federated credential: $FIC_NAME"
    az ad app federated-credential create \
        --id "$APP_ID" \
        --parameters "{
            \"name\": \"$FIC_NAME\",
            \"issuer\": \"https://token.actions.githubusercontent.com\",
            \"subject\": \"repo:${REPO_NAME}:environment:${ENVIRONMENT}\",
            \"audiences\": [\"api://AzureADTokenExchange\"]
        }"
else
    echo "Federated credential already exists: $FIC_NAME"
fi

# Also create a credential for pull requests (optional, can be restricted)
FIC_PR_NAME="github-actions-pr"
FIC_PR_EXISTS=$(az ad app federated-credential list --id "$APP_ID" --query "[?name=='$FIC_PR_NAME'].name" -o tsv)

if [ -z "$FIC_PR_EXISTS" ]; then
    echo "Creating federated credential for PRs: $FIC_PR_NAME"
    az ad app federated-credential create \
        --id "$APP_ID" \
        --parameters "{
            \"name\": \"$FIC_PR_NAME\",
            \"issuer\": \"https://token.actions.githubusercontent.com\",
            \"subject\": \"repo:${REPO_NAME}:pull_request\",
            \"audiences\": [\"api://AzureADTokenExchange\"]
        }"
else
    echo "Federated credential already exists: $FIC_PR_NAME"
fi

# Assign Contributor role scoped to the resource group
RESOURCE_GROUP="rg-ai-platform-${ENVIRONMENT}-san-001"
echo "Assigning Contributor role to resource group: $RESOURCE_GROUP"
RG_ID=$(az group show --name "$RESOURCE_GROUP" --query id -o tsv 2>/dev/null || echo "/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP")
az role assignment create \
    --assignee-object-id "$SP_ID" \
    --assignee-principal-type ServicePrincipal \
    --role Contributor \
    --scope "$RG_ID" \
    2>/dev/null || echo "Role assignment may already exist"

echo ""
echo "=== Setup Complete ==="
echo "AZURE_CLIENT_ID: $APP_ID"
echo "AZURE_TENANT_ID: $TENANT_ID"
echo "AZURE_SUBSCRIPTION_ID: $SUBSCRIPTION_ID"
echo ""
echo "Add these as secrets in your GitHub repository:"
echo "  - AZURE_CLIENT_ID"
echo "  - AZURE_TENANT_ID"
echo "  - AZURE_SUBSCRIPTION_ID"
echo "  - POSTGRES_ADMIN_PASSWORD (generate a strong password)"
