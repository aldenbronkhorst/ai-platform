@description('Resource group name')
param resourceGroupName string

@description('Budget amount in local currency')
param budgetAmount int

@description('Tags for resources')
param tags object

var budgetName = 'budget-${resourceGroupName}'

resource budget 'Microsoft.Consumption/budgets@2023-05-01' = {
  name: budgetName
  properties: {
    category: 'Cost'
    amount: budgetAmount
    timeGrain: 'Monthly'
    timePeriod: {
      startDate: '${utcNow('yyyy-MM')}-01'
      endDate: '${string(int(utcNow('yyyy')) + 1)}-${utcNow('MM')}-01'
    }
    notifications: {
      Alert50: {
        enabled: true
        operator: 'GreaterThan'
        threshold: 50
        contactEmails: [
          'alden@lotslotsmore.com'
        ]
      }
      Alert80: {
        enabled: true
        operator: 'GreaterThan'
        threshold: 80
        contactEmails: [
          'alden@lotslotsmore.com'
        ]
      }
      Alert100: {
        enabled: true
        operator: 'GreaterThan'
        threshold: 100
        contactEmails: [
          'alden@lotslotsmore.com'
        ]
      }
    }
  }
}

output budgetName string = budget.name
