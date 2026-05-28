@description('Resource group name')
param resourceGroupName string

@description('Budget amount in local currency')
param budgetAmount int

@description('Budget start date (yyyy-MM-dd)')
param startDate string

@description('Budget end date (yyyy-MM-dd)')
param endDate string

var budgetName = 'budget-${resourceGroupName}'

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
