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

@description('API managed identity client ID')
param apiManagedIdentityClientId string

@description('API managed identity resource ID')
param apiManagedIdentityResourceId string

@description('ACR login server')
param acrLoginServer string

@description('AI Core API container image tag')
param apiImageTag string = 'latest'

@description('Odoo Connector container image tag')
param odooConnectorImageTag string = 'latest'

@description('Application Insights connection string')
param appInsightsConnectionString string

@description('Log Analytics workspace name')
param logAnalyticsWorkspaceName string

@description('Key Vault URI')
param keyVaultUri string

@description('Storage account name')
param storageAccountName string

@description('Service Bus namespace')
param serviceBusNamespace string

@description('PostgreSQL host')
param postgresHost string

@description('PostgreSQL database name')
param postgresDatabaseName string

@description('PostgreSQL admin username')
param postgresAdminUsername string

var environmentName = 'cae-${workload}-${environment}-${regionCode}-${instance}'
var containerAppName = 'ca-${workload}-api-${environment}-${regionCode}-${instance}'
var odooConnectorAppName = 'ca-${workload}-odoo-connector-${environment}-${regionCode}-${instance}'
var containerImage = '${acrLoginServer}/ai-core-api:${apiImageTag}'
var odooConnectorImage = '${acrLoginServer}/odoo-connector-api:${odooConnectorImageTag}'

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2022-10-01' existing = {
  name: logAnalyticsWorkspaceName
}

resource containerAppsEnvironment 'Microsoft.App/managedEnvironments@2023-05-01' = {
  name: environmentName
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
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
    managedEnvironmentId: containerAppsEnvironment.id
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
      secrets: [
        {
          name: 'keyvault-dsn'
          keyVaultUrl: '${keyVaultUri}secrets/postgres-admin-password'
          identity: apiManagedIdentityResourceId
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'ai-core-api'
          image: containerImage
          env: [
            { name: 'POSTGRES_HOST', value: postgresHost }
            { name: 'POSTGRES_DATABASE', value: postgresDatabaseName }
            { name: 'POSTGRES_USERNAME', value: postgresAdminUsername }
            { name: 'POSTGRES_PASSWORD', secretRef: 'keyvault-dsn' }
            { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsightsConnectionString }
            { name: 'AZURE_CLIENT_ID', value: apiManagedIdentityClientId }
            { name: 'AZURE_STORAGE_ACCOUNT', value: storageAccountName }
            { name: 'AZURE_SERVICE_BUS_NAMESPACE', value: serviceBusNamespace }
            { name: 'ENVIRONMENT', value: environment }
            { name: 'VERSION', value: '1.0.0' }
          ]
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
          probes: [
            {
              type: 'Liveness'
              httpGet: {
                path: '/health'
                port: 8000
              }
              initialDelaySeconds: 30
              periodSeconds: 30
              timeoutSeconds: 5
              failureThreshold: 3
            }
            {
              type: 'Readiness'
              httpGet: {
                path: '/health'
                port: 8000
              }
              initialDelaySeconds: 10
              periodSeconds: 10
              timeoutSeconds: 5
              failureThreshold: 3
            }
          ]
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: 2
        rules: [
          {
            name: 'http-rule'
            custom: {
              type: 'http'
              metadata: {
                concurrentRequests: '50'
              }
            }
          }
        ]
      }
    }
  }
}

resource odooConnectorApp 'Microsoft.App/containerApps@2023-05-01' = {
  name: odooConnectorAppName
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${apiManagedIdentityResourceId}': {}
    }
  }
  properties: {
    managedEnvironmentId: containerAppsEnvironment.id
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
    }
    template: {
      containers: [
        {
          name: 'odoo-connector'
          image: odooConnectorImage
          env: [
            { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsightsConnectionString }
            { name: 'ENVIRONMENT', value: environment }
            { name: 'VERSION', value: '1.0.0' }
          ]
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
          probes: [
            {
              type: 'Liveness'
              httpGet: {
                path: '/health'
                port: 8000
              }
              initialDelaySeconds: 30
              periodSeconds: 30
              timeoutSeconds: 5
              failureThreshold: 3
            }
            {
              type: 'Readiness'
              httpGet: {
                path: '/health'
                port: 8000
              }
              initialDelaySeconds: 10
              periodSeconds: 10
              timeoutSeconds: 5
              failureThreshold: 3
            }
          ]
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: 2
        rules: [
          {
            name: 'http-rule'
            custom: {
              type: 'http'
              metadata: {
                concurrentRequests: '50'
              }
            }
          }
        ]
      }
    }
  }
}

output containerAppName string = containerApp.name
output environmentName string = containerAppsEnvironment.name
output apiUrl string = 'https://${containerApp.properties.configuration.ingress.fqdn}'
output fqdn string = containerApp.properties.configuration.ingress.fqdn
output odooConnectorAppName string = odooConnectorApp.name
output odooConnectorUrl string = 'https://${odooConnectorApp.properties.configuration.ingress.fqdn}'
output environmentId string = containerAppsEnvironment.id
