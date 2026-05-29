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

var identityName = 'id-${workload}-api-${environment}-${regionCode}-${instance}'

resource apiIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: identityName
  location: location
  tags: tags
}

output apiManagedIdentityClientId string = apiIdentity.properties.clientId
output apiManagedIdentityPrincipalId string = apiIdentity.properties.principalId
output apiManagedIdentityResourceId string = apiIdentity.id
output apiManagedIdentityName string = apiIdentity.name
