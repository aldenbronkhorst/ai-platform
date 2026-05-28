@description('Base name for resources')
param baseName string

@description('Environment name')
param environment string

@description('Azure region')
param location string

@description('Tags for resources')
param tags object

// User-assigned managed identity for AI Core API
resource apiIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-${baseName}-api-${environment}'
  location: location
  tags: tags
}

// Outputs
output apiManagedIdentityClientId string = apiIdentity.properties.clientId
output apiManagedIdentityPrincipalId string = apiIdentity.properties.principalId
output apiManagedIdentityObjectId string = apiIdentity.properties.principalId
output apiManagedIdentityResourceId string = apiIdentity.id
output apiManagedIdentityName string = apiIdentity.name
