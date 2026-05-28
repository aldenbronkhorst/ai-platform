@description('Base name for resources')
param baseName string

@description('Environment name')
param environment string

@description('Azure region')
param location string

@description('Tags for resources')
param tags object

@description('Unique suffix for globally unique names')
param uniqueSuffix string

@description('API managed identity client ID')
param apiManagedIdentityClientId string

@description('API managed identity principal ID')
param apiManagedIdentityPrincipalId string

@description('API managed identity resource ID')
param apiManagedIdentityResourceId string

@description('Application Insights connection string')
param appInsightsConnectionString string

@description('Storage account name')
param storageAccountName string

@description('Service Bus namespace name')
param serviceBusNamespace string

@description('Key Vault URI')
param keyVaultUri string

var functionAppName = 'func-${baseName}-${environment}-${take(uniqueSuffix, 8)}'
var functionPlanName = 'plan-${baseName}-func-${environment}'

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-01-01' existing = {
  name: storageAccountName
}

resource functionPlan 'Microsoft.Web/serverfarms@2022-03-01' = {
  name: functionPlanName
  location: location
  tags: tags
  sku: {
    name: 'Y1'
    tier: 'Dynamic'
  }
  kind: 'functionapp'
  properties: {
    reserved: true
  }
}

resource functionAppResource 'Microsoft.Web/sites@2022-03-01' = {
  name: functionAppName
  location: location
  tags: tags
  kind: 'functionapp,linux'
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${apiManagedIdentityResourceId}': {}
    }
  }
  properties: {
    serverFarmId: functionPlan.id
    siteConfig: {
      linuxFxVersion: 'PYTHON|3.11'
      appSettings: [
        {
          name: 'AzureWebJobsStorage__accountName'
          value: storageAccountName
        }
        {
          name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
          value: appInsightsConnectionString
        }
        {
          name: 'FUNCTIONS_EXTENSION_VERSION'
          value: '~4'
        }
        {
          name: 'FUNCTIONS_WORKER_RUNTIME'
          value: 'python'
        }
        {
          name: 'AzureWebJobsFeatureFlags'
          value: 'EnableWorkerIndexing'
        }
        {
          name: 'AZURE_CLIENT_ID'
          value: apiManagedIdentityClientId
        }
        {
          name: 'KEY_VAULT_URI'
          value: keyVaultUri
        }
        {
          name: 'SERVICE_BUS_NAMESPACE'
          value: serviceBusNamespace
        }
      ]
    }
  }
}

output functionAppName string = functionAppResource.name
output functionAppId string = functionAppResource.id
output functionAppDefaultHost string = functionAppResource.properties.defaultHostName
