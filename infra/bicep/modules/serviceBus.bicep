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

var serviceBusName = 'sb-${baseName}-${environment}-${take(uniqueSuffix, 8)}'

resource serviceBusNamespace 'Microsoft.ServiceBus/namespaces@2022-10-01-preview' = {
  name: serviceBusName
  location: location
  tags: tags
  sku: {
    name: 'Standard'
    tier: 'Standard'
  }
  properties: {}
}

var queueNames = [
  'ai-jobs'
  'ai-runner-requests'
  'ai-artifact-processing'
  'ai-search-indexing'
  'ai-followups'
  'ai-notifications'
  'ai-automation-events'
]

resource queues 'Microsoft.ServiceBus/namespaces/queues@2022-10-01-preview' = [for queueName in queueNames: {
  parent: serviceBusNamespace
  name: queueName
  properties: {
    lockDuration: 'PT5M'
    maxSizeInMegabytes: 1024
    requiresDuplicateDetection: false
    requiresSession: false
    defaultMessageTimeToLive: 'P14D'
    deadLetteringOnMessageExpiration: true
    maxDeliveryCount: 10
    enablePartitioning: false
  }
}]

// Grant Azure Service Bus Data Sender to API managed identity
resource sbSenderRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(serviceBusNamespace.id, apiManagedIdentityPrincipalId, 'sbsender')
  scope: serviceBusNamespace
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '69a216fc-b8fb-44d8-bc22-1f3c2cd27a39') // Azure Service Bus Data Sender
    principalId: apiManagedIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// Grant Azure Service Bus Data Receiver to API managed identity
resource sbReceiverRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(serviceBusNamespace.id, apiManagedIdentityPrincipalId, 'sbreceiver')
  scope: serviceBusNamespace
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4f6d3b9b-027b-4f35-9a63-2c5f8c5c5c5c') // Azure Service Bus Data Receiver
    principalId: apiManagedIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

output namespaceName string = serviceBusNamespace.name
output namespaceId string = serviceBusNamespace.id
output namespaceEndpoint string = serviceBusNamespace.properties.serviceBusEndpoint
output queueNames array = queueNames
