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

var apiManagementName = 'apim-${workload}-${environment}-${regionCode}-${instance}'

resource apiManagement 'Microsoft.ApiManagement/service@2022-08-01' = {
  name: apiManagementName
  location: location
  tags: tags
  sku: {
    name: 'Consumption'
    capacity: 0
  }
  properties: {
    publisherName: 'AI Platform Team'
    publisherEmail: 'alden@lotslotsmore.com'
  }
}

output name string = apiManagement.name
output id string = apiManagement.id
output gatewayUrl string = apiManagement.properties.gatewayUrl
