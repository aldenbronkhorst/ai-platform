targetScope = 'subscription'

// Parameters
@description('Base name for all resources')
param baseName string = 'aiplatform'

@description('Environment name')
@allowed(['dev', 'staging', 'prod'])
param environment string = 'dev'

@description('Primary Azure region')
param location string = 'southafricanorth'

@description('Fallback region for services not available in primary')
param fallbackLocation string = 'westeurope'

@description('Tags for all resources')
param tags object = {
  project: 'ai-platform'
  environment: environment
  owner: 'alden'
  'managed-by': 'iac'
  'cost-center': 'ai-platform'
}

@description('PostgreSQL admin username')
param postgresAdminUsername string = 'aiplatformadmin'

@description('PostgreSQL admin password')
@secure()
param postgresAdminPassword string

@description('Budget amount for the environment')
param budgetAmount int = 2000

@description('Budget start date')
param budgetStartDate string = '${utcNow('yyyy-MM')}-01'

@description('Budget end date')
param budgetEndDate string = '${string(int(utcNow('yyyy')) + 1)}-${utcNow('MM')}-01'

// Variables
var resourceGroupName = 'rg-${baseName}-${environment}'
var uniqueSuffix = uniqueString(subscription().id, resourceGroupName)

// Resource Group
resource rg 'Microsoft.Resources/resourceGroups@2023-07-01' = {
  name: resourceGroupName
  location: location
  tags: tags
}

// Module: Managed Identity
module identity 'modules/managedIdentity.bicep' = {
  name: 'managedIdentityDeploy'
  scope: rg
  params: {
    baseName: baseName
    environment: environment
    location: location
    tags: tags
  }
}

// Module: Container Registry
module acr 'modules/containerRegistry.bicep' = {
  name: 'acrDeploy'
  scope: rg
  params: {
    baseName: baseName
    environment: environment
    location: location
    tags: tags
    uniqueSuffix: uniqueSuffix
    apiManagedIdentityPrincipalId: identity.outputs.apiManagedIdentityPrincipalId
  }
}

// Module: Key Vault
module keyVault 'modules/keyVault.bicep' = {
  name: 'keyVaultDeploy'
  scope: rg
  params: {
    baseName: baseName
    environment: environment
    location: location
    tags: tags
    uniqueSuffix: uniqueSuffix
    apiManagedIdentityPrincipalId: identity.outputs.apiManagedIdentityPrincipalId
    postgresAdminPassword: postgresAdminPassword
  }
}

// Module: Storage Account
module storage 'modules/storageAccount.bicep' = {
  name: 'storageDeploy'
  scope: rg
  params: {
    baseName: baseName
    environment: environment
    location: location
    tags: tags
    uniqueSuffix: uniqueSuffix
    apiManagedIdentityPrincipalId: identity.outputs.apiManagedIdentityPrincipalId
  }
}

// Module: PostgreSQL
module postgres 'modules/postgresql.bicep' = {
  name: 'postgresDeploy'
  scope: rg
  params: {
    baseName: baseName
    environment: environment
    location: location
    tags: tags
    uniqueSuffix: uniqueSuffix
    adminUsername: postgresAdminUsername
    adminPassword: postgresAdminPassword
  }
}

// Module: Application Insights and Log Analytics
module monitoring 'modules/appInsights.bicep' = {
  name: 'monitoringDeploy'
  scope: rg
  params: {
    baseName: baseName
    environment: environment
    location: location
    tags: tags
    uniqueSuffix: uniqueSuffix
  }
}

// Module: Service Bus
module serviceBus 'modules/serviceBus.bicep' = {
  name: 'serviceBusDeploy'
  scope: rg
  params: {
    baseName: baseName
    environment: environment
    location: location
    tags: tags
    uniqueSuffix: uniqueSuffix
    apiManagedIdentityPrincipalId: identity.outputs.apiManagedIdentityPrincipalId
  }
}

// Module: Container Apps Environment and API App
module containerApps 'modules/containerApps.bicep' = {
  name: 'containerAppsDeploy'
  scope: rg
  params: {
    baseName: baseName
    environment: environment
    location: location
    tags: tags
    uniqueSuffix: uniqueSuffix
    apiManagedIdentityClientId: identity.outputs.apiManagedIdentityClientId
    apiManagedIdentityResourceId: identity.outputs.apiManagedIdentityResourceId
    acrLoginServer: acr.outputs.loginServer
    containerImage: '${acr.outputs.loginServer}/ai-core-api:latest'
    appInsightsConnectionString: monitoring.outputs.connectionString
    logAnalyticsWorkspaceName: monitoring.outputs.logAnalyticsWorkspaceName
    keyVaultUri: keyVault.outputs.vaultUri
    storageAccountName: storage.outputs.storageAccountName
    serviceBusNamespace: serviceBus.outputs.namespaceName
    postgresHost: postgres.outputs.fqdn
    postgresDatabaseName: postgres.outputs.databaseName
    postgresAdminUsername: postgresAdminUsername
  }
}

// Module: Function App (Durable Functions)
module functionApp 'modules/functionApp.bicep' = {
  name: 'functionAppDeploy'
  scope: rg
  params: {
    baseName: baseName
    environment: environment
    location: location
    tags: tags
    uniqueSuffix: uniqueSuffix
    apiManagedIdentityClientId: identity.outputs.apiManagedIdentityClientId
    apiManagedIdentityPrincipalId: identity.outputs.apiManagedIdentityPrincipalId
    apiManagedIdentityResourceId: identity.outputs.apiManagedIdentityResourceId
    appInsightsConnectionString: monitoring.outputs.connectionString
    storageAccountName: storage.outputs.storageAccountName
    serviceBusNamespace: serviceBus.outputs.namespaceName
    keyVaultUri: keyVault.outputs.vaultUri
  }
}

// Module: AI Search
module aiSearch 'modules/searchService.bicep' = {
  name: 'aiSearchDeploy'
  scope: rg
  params: {
    baseName: baseName
    environment: environment
    location: location
    tags: tags
    uniqueSuffix: uniqueSuffix
    apiManagedIdentityPrincipalId: identity.outputs.apiManagedIdentityPrincipalId
  }
}

// Module: API Management
module apiManagement 'modules/apiManagement.bicep' = {
  name: 'apiManagementDeploy'
  scope: rg
  params: {
    baseName: baseName
    environment: environment
    location: location
    tags: tags
    uniqueSuffix: uniqueSuffix
    apiBackendUrl: containerApps.outputs.apiUrl
  }
}

// Module: Budget
module budget 'modules/budget.bicep' = {
  name: 'budgetDeploy'
  scope: rg
  params: {
    resourceGroupName: rg.name
    budgetAmount: budgetAmount
    startDate: budgetStartDate
    endDate: budgetEndDate
  }
}

// Outputs
output resourceGroupName string = rg.name
output acrLoginServer string = acr.outputs.loginServer
output acrName string = acr.outputs.name
output keyVaultName string = keyVault.outputs.name
output keyVaultUri string = keyVault.outputs.vaultUri
output storageAccountName string = storage.outputs.storageAccountName
output postgresFqdn string = postgres.outputs.fqdn
output postgresDatabaseName string = postgres.outputs.databaseName
output appInsightsName string = monitoring.outputs.name
output serviceBusNamespace string = serviceBus.outputs.namespaceName
output containerAppName string = containerApps.outputs.containerAppName
output containerAppsEnvironmentName string = containerApps.outputs.environmentName
output functionAppName string = functionApp.outputs.functionAppName
output aiSearchName string = aiSearch.outputs.name
output apiManagementName string = apiManagement.outputs.name
output apiManagementGatewayUrl string = apiManagement.outputs.gatewayUrl
output apiManagedIdentityClientId string = identity.outputs.apiManagedIdentityClientId
output apiManagedIdentityPrincipalId string = identity.outputs.apiManagedIdentityPrincipalId
output apiUrl string = containerApps.outputs.apiUrl
