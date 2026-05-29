targetScope = 'subscription'

// Naming parameters
@description('Workload name')
param workload string = 'ai-platform'

@description('Environment name')
@allowed(['dev', 'staging', 'prod'])
param environment string = 'prod'

@description('Primary Azure region')
param location string = 'southafricanorth'

@description('Region code abbreviation')
param regionCode string = 'san'

@description('Instance number')
param instance string = '001'

// Tags
param tags object = {
  application: workload
  environment: environment
  region: location
  'region-code': regionCode
  owner: 'alden'
  'managed-by': 'iac'
  'cost-center': workload
  'business-criticality': 'pilot-production'
  'data-classification': 'company-internal'
}

// Admin credentials
@description('PostgreSQL admin username')
param postgresAdminUsername string = 'aiplatformadmin'

@description('PostgreSQL admin password')
@secure()
param postgresAdminPassword string

@description('Budget amount')
param budgetAmount int = 3000

@description('Budget start date')
param budgetStartDate string = '${utcNow('yyyy-MM')}-01'

@description('Budget end date')
param budgetEndDate string = '${string(int(utcNow('yyyy')) + 1)}-${utcNow('MM')}-01'

@description('AI Core API container image tag')
param apiImageTag string = 'latest'

@description('Odoo Connector container image tag')
param odooConnectorImageTag string = 'latest'

// Naming helper variables
var resourceGroupName = 'rg-${workload}-${environment}-${regionCode}-${instance}'

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
    workload: workload
    environment: environment
    regionCode: regionCode
    instance: instance
    location: location
    tags: tags
  }
}

// Module: Container Registry
module acr 'modules/containerRegistry.bicep' = {
  name: 'acrDeploy'
  scope: rg
  params: {
    workload: workload
    environment: environment
    regionCode: regionCode
    instance: instance
    location: location
    tags: tags
    apiManagedIdentityPrincipalId: identity.outputs.apiManagedIdentityPrincipalId
  }
}

// Module: Key Vault
module keyVault 'modules/keyVault.bicep' = {
  name: 'keyVaultDeploy'
  scope: rg
  params: {
    workload: workload
    environment: environment
    regionCode: regionCode
    instance: instance
    location: location
    tags: tags
    apiManagedIdentityPrincipalId: identity.outputs.apiManagedIdentityPrincipalId
    postgresAdminPassword: postgresAdminPassword
  }
}

// Module: Storage Account
module storage 'modules/storageAccount.bicep' = {
  name: 'storageDeploy'
  scope: rg
  params: {
    workload: workload
    environment: environment
    regionCode: regionCode
    instance: instance
    location: location
    tags: tags
    apiManagedIdentityPrincipalId: identity.outputs.apiManagedIdentityPrincipalId
  }
}

// Module: PostgreSQL
module postgres 'modules/postgresql.bicep' = {
  name: 'postgresDeploy'
  scope: rg
  params: {
    workload: workload
    environment: environment
    regionCode: regionCode
    instance: instance
    location: location
    tags: tags
    adminUsername: postgresAdminUsername
    adminPassword: postgresAdminPassword
  }
}

// Module: Application Insights and Log Analytics
module monitoring 'modules/appInsights.bicep' = {
  name: 'monitoringDeploy'
  scope: rg
  params: {
    workload: workload
    environment: environment
    regionCode: regionCode
    instance: instance
    location: location
    tags: tags
  }
}

// Module: Service Bus
module serviceBus 'modules/serviceBus.bicep' = {
  name: 'serviceBusDeploy'
  scope: rg
  params: {
    workload: workload
    environment: environment
    regionCode: regionCode
    instance: instance
    location: location
    tags: tags
    apiManagedIdentityPrincipalId: identity.outputs.apiManagedIdentityPrincipalId
  }
}

// Module: Container Apps Environment and API App
module containerApps 'modules/containerApps.bicep' = {
  name: 'containerAppsDeploy'
  scope: rg
  params: {
    workload: workload
    environment: environment
    regionCode: regionCode
    instance: instance
    location: location
    tags: tags
    apiManagedIdentityClientId: identity.outputs.apiManagedIdentityClientId
    apiManagedIdentityResourceId: identity.outputs.apiManagedIdentityResourceId
    acrLoginServer: acr.outputs.loginServer
    apiImageTag: apiImageTag
    odooConnectorImageTag: odooConnectorImageTag
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

// Module: AI Search
module aiSearch 'modules/searchService.bicep' = {
  name: 'aiSearchDeploy'
  scope: rg
  params: {
    workload: workload
    environment: environment
    regionCode: regionCode
    instance: instance
    location: location
    tags: tags
    apiManagedIdentityPrincipalId: identity.outputs.apiManagedIdentityPrincipalId
  }
}

// Module: API Management
module apiManagement 'modules/apiManagement.bicep' = {
  name: 'apiManagementDeploy'
  scope: rg
  params: {
    workload: workload
    environment: environment
    regionCode: regionCode
    instance: instance
    location: location
    tags: tags
  }
}

// Module: Budget
module budget 'modules/budget.bicep' = {
  name: 'budgetDeploy'
  scope: rg
  params: {
    workload: workload
    environment: environment
    regionCode: regionCode
    instance: instance
    budgetAmount: budgetAmount
    startDate: budgetStartDate
    endDate: budgetEndDate
  }
}

// Module: Static Web App (Web Portal)
module staticWebApp 'modules/staticWebApp.bicep' = {
  name: 'staticWebAppDeploy'
  scope: rg
  params: {
    workload: workload
    environment: environment
    regionCode: regionCode
    instance: instance
    location: location
    tags: tags
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
output aiSearchName string = aiSearch.outputs.name
output apiManagementName string = apiManagement.outputs.name
output apiManagementGatewayUrl string = apiManagement.outputs.gatewayUrl
output apiManagedIdentityClientId string = identity.outputs.apiManagedIdentityClientId
output apiManagedIdentityPrincipalId string = identity.outputs.apiManagedIdentityPrincipalId
output apiUrl string = containerApps.outputs.apiUrl
output odooConnectorAppName string = containerApps.outputs.odooConnectorAppName
output odooConnectorUrl string = containerApps.outputs.odooConnectorUrl
output staticSiteName string = staticWebApp.outputs.name
output staticSiteDefaultHostname string = staticWebApp.outputs.defaultHostname
