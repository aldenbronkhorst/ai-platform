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

@description('API managed identity principal ID')
param apiManagedIdentityPrincipalId string

var searchName = 'srch-${baseName}-${environment}-${take(uniqueSuffix, 8)}'

resource searchService 'Microsoft.Search/searchServices@2023-11-01' = {
  name: searchName
  location: location
  tags: tags
  sku: {
    name: 'basic'
  }
  properties: {
    replicaCount: 1
    partitionCount: 1
    hostingMode: 'default'
    publicNetworkAccess: 'Enabled'
    authOptions: {
      aadOrApiKey: {
        aadAuthFailureMode: 'http401WithBearerChallenge'
      }
    }
  }
}

// Grant Search Index Data Contributor to API managed identity
resource searchIndexDataContributorRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(searchService.id, apiManagedIdentityPrincipalId, 'searchindexcontributor')
  scope: searchService
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '8ebe5a00-799e-43f5-93ac-243d3dce84a7') // Search Index Data Contributor
    principalId: apiManagedIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// Grant Search Service Contributor to API managed identity
resource searchServiceContributorRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(searchService.id, apiManagedIdentityPrincipalId, 'searchservicecontributor')
  scope: searchService
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7ca78c08-252a-4471-8644-bb5ff32d4ba0') // Search Service Contributor
    principalId: apiManagedIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

output name string = searchService.name
output id string = searchService.id
output endpoint string = 'https://${searchService.name}.search.windows.net'
