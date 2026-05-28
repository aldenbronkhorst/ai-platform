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

@description('Backend URL for AI Core API')
param apiBackendUrl string

var apimName = 'apim-${baseName}-${environment}-${take(uniqueSuffix, 8)}'
var apimPublisherEmail = 'alden@lotslotsmore.com'
var apimPublisherName = 'Lots Lots More AI Platform'

resource apiManagement 'Microsoft.ApiManagement/service@2022-08-01' = {
  name: apimName
  location: location
  tags: tags
  sku: {
    name: 'Consumption'
    capacity: 0
  }
  properties: {
    publisherEmail: apimPublisherEmail
    publisherName: apimPublisherName
  }
}

// Backend for AI Core API
resource apiBackend 'Microsoft.ApiManagement/service/backends@2022-08-01' = {
  parent: apiManagement
  name: 'ai-core-api'
  properties: {
    title: 'AI Core API'
    description: 'Backend for AI Core API running on Azure Container Apps'
    url: apiBackendUrl
    protocol: 'http'
  }
}

// API definition
resource apiDefinition 'Microsoft.ApiManagement/service/apis@2022-08-01' = {
  parent: apiManagement
  name: 'ai-platform-api'
  properties: {
    displayName: 'AI Platform API'
    description: 'Main API for the AI Platform'
    serviceUrl: apiBackendUrl
    path: ''
    protocols: [
      'https'
    ]
    subscriptionRequired: true
  }
}

// Health endpoint operation
resource healthOperation 'Microsoft.ApiManagement/service/apis/operations@2022-08-01' = {
  parent: apiDefinition
  name: 'health'
  properties: {
    displayName: 'Health Check'
    method: 'GET'
    urlTemplate: '/health'
    description: 'Health check endpoint'
  }
}

output name string = apiManagement.name
output id string = apiManagement.id
output gatewayUrl string = apiManagement.properties.gatewayUrl
output managementApiUrl string = apiManagement.properties.managementApiUrl
