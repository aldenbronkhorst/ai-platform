param location string = resourceGroup().location
param tags object = {}

param workload string
param environment string
param regionCode string
param instance string

param vnetName string
param vnetResourceGroupName string
param subnetName string = 'private-endpoints'

param keyVaultId string
param storageAccountId string
param serviceBusNamespaceId string
param aiSearchId string
param postgresServerId string
param deploySearch bool = false

var namePrefix = '${workload}-${environment}-${regionCode}-${instance}'

resource vnet 'Microsoft.Network/virtualNetworks@2023-11-01' existing = {
  name: vnetName
  scope: resourceGroup(vnetResourceGroupName)
}

resource subnet 'Microsoft.Network/virtualNetworks/subnets@2023-11-01' existing = {
  parent: vnet
  name: subnetName
}

resource privateEndpointKeyVault 'Microsoft.Network/privateEndpoints@2023-11-01' = {
  name: 'pe-${namePrefix}-kv'
  location: location
  tags: tags
  properties: {
    subnet: {
      id: subnet.id
    }
    privateLinkServiceConnections: [
      {
        name: 'keyvault'
        properties: {
          privateLinkServiceId: keyVaultId
          groupIds: ['vault']
        }
      }
    ]
  }
}

resource privateEndpointStorageBlob 'Microsoft.Network/privateEndpoints@2023-11-01' = {
  name: 'pe-${namePrefix}-st-blob'
  location: location
  tags: tags
  properties: {
    subnet: {
      id: subnet.id
    }
    privateLinkServiceConnections: [
      {
        name: 'storageblob'
        properties: {
          privateLinkServiceId: storageAccountId
          groupIds: ['blob']
        }
      }
    ]
  }
}

resource privateEndpointServiceBus 'Microsoft.Network/privateEndpoints@2023-11-01' = {
  name: 'pe-${namePrefix}-sb'
  location: location
  tags: tags
  properties: {
    subnet: {
      id: subnet.id
    }
    privateLinkServiceConnections: [
      {
        name: 'servicebus'
        properties: {
          privateLinkServiceId: serviceBusNamespaceId
          groupIds: ['namespace']
        }
      }
    ]
  }
}

resource privateEndpointAIsearch 'Microsoft.Network/privateEndpoints@2023-11-01' = if (deploySearch) {
  name: 'pe-${namePrefix}-srch'
  location: location
  tags: tags
  properties: {
    subnet: {
      id: subnet.id
    }
    privateLinkServiceConnections: [
      {
        name: 'search'
        properties: {
          privateLinkServiceId: aiSearchId
          groupIds: ['searchService']
        }
      }
    ]
  }
}

resource privateEndpointPostgres 'Microsoft.Network/privateEndpoints@2023-11-01' = {
  name: 'pe-${namePrefix}-psql'
  location: location
  tags: tags
  properties: {
    subnet: {
      id: subnet.id
    }
    privateLinkServiceConnections: [
      {
        name: 'postgres'
        properties: {
          privateLinkServiceId: postgresServerId
          groupIds: ['postgresqlServer']
        }
      }
    ]
  }
}
