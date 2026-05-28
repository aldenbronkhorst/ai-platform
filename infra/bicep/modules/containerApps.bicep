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

@description('API managed identity resource ID')
param apiManagedIdentityResourceId string

@description('ACR login server')
param acrLoginServer string

@description('Container image to deploy')
param containerImage string = 'mcr.microsoft.com/k8se/quickstart:latest'

@description('Application Insights connection string')
param appInsightsConnectionString string

@description('Log Analytics Workspace Name')
param logAnalyticsWorkspaceName string

@description('Key Vault URI')
param keyVaultUri string

@description('Storage account name')
param storageAccountName string

@description('Service Bus namespace name')
param serviceBusNamespace string

@description('PostgreSQL host FQDN')
param postgresHost string

@description('PostgreSQL database name')
param postgresDatabaseName string

@description('PostgreSQL admin username')
param postgresAdminUsername string

@description('PostgreSQL admin password')
@secure()
param postgresAdminPassword string

var envName = 'cae-${baseName}-${environment}-${take(uniqueSuffix, 8)}'
var containerAppName = 'ca-${baseName}-api-${environment}'

resource containerAppsEnv 'Microsoft.App/managedEnvironments@2023-05-01' = {
  name: envName
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: reference(resourceId('Microsoft.OperationalInsights/workspaces', logAnalyticsWorkspaceName), '2022-10-01').customerId
        sharedKey: listKeys(resourceId('Microsoft.OperationalInsights/workspaces', logAnalyticsWorkspaceName), '2022-10-01').primarySharedKey
      }
    }
  }
}

resource containerApp 'Microsoft.App/containerApps@2023-05-01' = {
  name: containerAppName
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${apiManagedIdentityResourceId}': {}
    }
  }
  properties: {
    managedEnvironmentId: containerAppsEnv.id
    configuration: {
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
        allowInsecure: false
      }
      registries: [
        {
          server: acrLoginServer
          identity: apiManagedIdentityResourceId
        }
      ]
      secrets: []
    }
    template: {
      revisionSuffix: ''
      containers: [
        {
          name: 'ai-core-api'
          image: containerImage
          env: [
            {
              name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
              value: appInsightsConnectionString
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
              name: 'STORAGE_ACCOUNT_NAME'
              value: storageAccountName
            }
            {
              name: 'SERVICE_BUS_NAMESPACE'
              value: serviceBusNamespace
            }
            {
              name: 'POSTGRES_HOST'
              value: postgresHost
            }
            {
              name: 'POSTGRES_DB'
              value: postgresDatabaseName
            }
            {
              name: 'POSTGRES_USER'
              value: postgresAdminUsername
            }
            {
              name: 'POSTGRES_PASSWORD'
              value: postgresAdminPassword
            }
          ]
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          probes: [
            {
              type: 'Liveness'
              httpGet: {
                path: '/health'
                port: 8000
              }
              initialDelaySeconds: 10
              periodSeconds: 30
            }
            {
              type: 'Readiness'
              httpGet: {
                path: '/health'
                port: 8000
              }
              initialDelaySeconds: 5
              periodSeconds: 10
            }
          ]
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 3
        rules: []
      }
    }
  }
}

output environmentName string = containerAppsEnv.name
output environmentId string = containerAppsEnv.id
output containerAppName string = containerApp.name
output containerAppId string = containerApp.id
output apiUrl string = 'https://${containerApp.properties.configuration.ingress.fqdn}'
