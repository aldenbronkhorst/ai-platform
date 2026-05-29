@description('Workload name')
param workload string

@description('Environment name')
param environment string

@description('Region code')
param regionCode string

@description('Instance number')
param instance string

@description('Azure region')
param location string

@description('Tags for resources')
param tags object

@description('API managed identity client ID')
param apiManagedIdentityClientId string

@description('API managed identity resource ID')
param apiManagedIdentityResourceId string

@description('Application Insights connection string')
param appInsightsConnectionString string

@description('Storage account name for function app')
param storageAccountName string

@description('Service Bus namespace')
param serviceBusNamespace string

@description('Key Vault URI')
param keyVaultUri string

var appServicePlanName = 'asp-${workload}-func-${environment}-${regionCode}-${instance}'
var functionAppName = 'func-${workload}-${environment}-${regionCode}-${instance}'

resource appServicePlan 'Microsoft.Web/serverfarms@2022-03-01' = {
  name: appServicePlanName
  location: location
  tags: tags
  sku: {
    name: 'Y1'
    tier: 'Dynamic'
  }
  kind: 'functionapp'
  properties: {}
}

resource functionAppResource 'Microsoft.Web/sites@2022-03-01' = {
  name: functionAppName
  location: location
  tags: tags
  kind: 'functionapp'
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${apiManagedIdentityResourceId}': {}
    }
  }
  properties: {
    serverFarmId: appServicePlan.id
    siteConfig: {
      appSettings: [
        { name: 'FUNCTIONS_EXTENSION_VERSION', value: '~4' }
        { name: 'FUNCTIONS_WORKER_RUNTIME', value: 'python' }
        { name: 'AzureWebJobsStorage', value: 'DefaultEndpointsProtocol=https;AccountName=${storageAccountName};EndpointSuffix=${az.environment().suffixes.storage};' }
        { name: 'WEBSITE_CONTENTAZUREFILECONNECTIONSTRING', value: 'DefaultEndpointsProtocol=https;AccountName=${storageAccountName};EndpointSuffix=${az.environment().suffixes.storage};' }
        { name: 'WEBSITE_CONTENTSHARE', value: toLower(functionAppName) }
        { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsightsConnectionString }
        { name: 'AZURE_CLIENT_ID', value: apiManagedIdentityClientId }
        { name: 'AZURE_SERVICE_BUS_NAMESPACE', value: serviceBusNamespace }
        { name: 'AZURE_KEY_VAULT_URI', value: keyVaultUri }
      ]
      pythonVersion: '3.11'
      use32BitWorkerProcess: false
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      scmMinTlsVersion: '1.2'
    }
    httpsOnly: true
  }
}

output functionAppName string = functionAppResource.name
output functionAppId string = functionAppResource.id
output defaultHostName string = functionAppResource.properties.defaultHostName
