@description('Workload name')
param workload string

@description('Environment name')
param environment string

@description('Region code')
param regionCode string

@description('Instance number')
param instance string

@description('Budget amount in local currency')
param budgetAmount int

@description('Budget start date')
param startDate string

@description('Budget end date')
param endDate string

var budgetName = 'budget-${workload}-${environment}-${regionCode}-${instance}'

resource budget 'Microsoft.Consumption/budgets@2023-05-01' = {
  name: budgetName
  properties: {
    category: 'Cost'
    amount: budgetAmount
    timeGrain: 'Monthly'
    timePeriod: {
      startDate: startDate
      endDate: endDate
    }
    notifications: {
      '80Percent': {
        enabled: true
        operator: 'GreaterThan'
        threshold: 80
        contactEmails: ['alden@lotslotsmore.com']
        contactRoles: ['Owner']
      }
      '100Percent': {
        enabled: true
        operator: 'GreaterThan'
        threshold: 100
        contactEmails: ['alden@lotslotsmore.com']
        contactRoles: ['Owner']
      }
    }
  }
}

output budgetName string = budget.name
