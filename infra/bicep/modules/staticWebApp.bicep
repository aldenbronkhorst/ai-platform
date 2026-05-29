@description('Workload name')
param workload string

@description('Environment name')
param environment string

@description('Region code')
param regionCode string

@description('Instance number')
param instance string

@description('Azure region')
param location string = 'global' // Static Web Apps are globally distributed, but metadata goes to a supported region

@description('Tags for resources')
param tags object

var staticSiteName = 'swa-${workload}-${environment}-${regionCode}-${instance}'

resource staticSite 'Microsoft.Web/staticSites@2022-09-01' = {
  name: staticSiteName
  location: location == 'southafricanorth' ? 'westeurope' : location // SWA is not available in southafricanorth, so we metadata-host in westeurope (the content itself is globally replicated on CDNs anyway)
  tags: tags
  sku: {
    name: 'Free'
    tier: 'Free'
  }
  properties: {
    allowConfigFileUpdates: true
    enterpriseGradeCdnStatus: 'Disabled'
  }
}

output name string = staticSite.name
output defaultHostname string = staticSite.properties.defaultHostname
