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

@description('API managed identity principal ID')
param apiManagedIdentityPrincipalId string

@description('PostgreSQL admin password to store in Key Vault')
@secure()
param postgresAdminPassword string

@description('API key for AI Core API authentication')
@secure()
param apiKey string = newGuid()

@description('API key for Odoo Connector internal authentication')
@secure()
param odooConnectorApiKey string = newGuid()

var sanitizedWorkload = replace(workload, '-', '')
var keyVaultName = 'kv${sanitizedWorkload}${environment}${regionCode}${instance}'

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: keyVaultName
  location: location
  tags: tags
  properties: {
    sku: {
      family: 'A'
      name: 'standard'
    }
    tenantId: subscription().tenantId
    enableRbacAuthorization: true
    enabledForTemplateDeployment: true
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      defaultAction: 'Deny'
      bypass: 'AzureServices'
    }
  }
}

resource kvSecretsUserRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, apiManagedIdentityPrincipalId, 'kvsecretsuser')
  scope: keyVault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')
    principalId: apiManagedIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

resource postgresPasswordSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'postgres-admin-password'
  properties: {
    value: postgresAdminPassword
  }
}

resource apiKeySecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'api-key'
  properties: {
    value: apiKey
  }
}

resource odooConnectorApiKeySecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'odoo-connector-api-key'
  properties: {
    value: odooConnectorApiKey
  }
}

output vaultUri string = keyVault.properties.vaultUri
output name string = keyVault.name
output id string = keyVault.id
