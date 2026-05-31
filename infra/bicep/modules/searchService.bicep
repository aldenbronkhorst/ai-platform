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

@description('Search service SKU name')
@allowed(['free', 'basic', 'standard'])
param skuName string = 'free'

var searchName = 'srch-${workload}-${environment}-${regionCode}-${instance}'

resource search 'Microsoft.Search/searchServices@2022-09-01' = {
  name: searchName
  location: location
  tags: tags
  sku: {
    name: skuName
  }
  properties: {
    replicaCount: 1
    partitionCount: 1
    publicNetworkAccess: 'disabled'
    networkRuleSet: {
      ipRules: []
    }
    hostingMode: 'default'
    authOptions: {
      aadOrApiKey: {
        aadAuthFailureMode: 'http401WithBearerChallenge'
      }
    }
  }
}

resource searchContributorRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(search.id, apiManagedIdentityPrincipalId, 'searchcontributor')
  scope: search
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7ca78c08-252a-4471-8644-bb5ff32d4ba0')
    principalId: apiManagedIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

resource searchIndexDataContributorRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(search.id, apiManagedIdentityPrincipalId, 'searchindexdatacontributor')
  scope: search
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '8ebe5a1e-416e-445e-9391-7f9e8a719c8d')
    principalId: apiManagedIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

resource searchIndexDataReaderRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(search.id, apiManagedIdentityPrincipalId, 'searchindexdatareader')
  scope: search
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '14071b83-acda-42c4-9023-ad4e44928aa2')
    principalId: apiManagedIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

output name string = search.name
output id string = search.id
output endpoint string = 'https://${search.name}.search.windows.net'
