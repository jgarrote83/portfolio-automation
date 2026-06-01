// Event Grid System Topic on the storage account + subscription that fires the
// analyzer function whenever a new blob lands under daily-snapshots/.
//
// Required because Flex Consumption SKU only supports EventGrid-sourced blob
// triggers (polling-based BlobTrigger is not available). See:
//   https://aka.ms/blob-trigger-eg

param location string
param storageAccountName string
param functionAppName string
@description('Name of the Python function with @app.blob_trigger(..., source="EventGrid").')
param analyzerFunctionName string = 'analyzer'

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageAccountName
}

resource functionApp 'Microsoft.Web/sites@2024-04-01' existing = {
  name: functionAppName
}

// System Topic for the storage account.
resource systemTopic 'Microsoft.EventGrid/systemTopics@2024-06-01-preview' = {
  name: '${storageAccountName}-snapshots-topic'
  location: location
  properties: {
    source: storageAccount.id
    topicType: 'Microsoft.Storage.StorageAccounts'
  }
}

// Subscription → Azure Function (analyzer). Endpoint type 'AzureFunction'
// uses the function's resource id; Event Grid uses the system key to invoke.
resource analyzerSubscription 'Microsoft.EventGrid/systemTopics/eventSubscriptions@2024-06-01-preview' = {
  parent: systemTopic
  name: 'analyzer-on-snapshot'
  properties: {
    destination: {
      endpointType: 'AzureFunction'
      properties: {
        resourceId: '${functionApp.id}/functions/${analyzerFunctionName}'
        maxEventsPerBatch: 1
        preferredBatchSizeInKilobytes: 64
      }
    }
    filter: {
      includedEventTypes: [
        'Microsoft.Storage.BlobCreated'
      ]
      subjectBeginsWith: '/blobServices/default/containers/daily-snapshots/'
      subjectEndsWith: '.json'
    }
    eventDeliverySchema: 'EventGridSchema'
    retryPolicy: {
      maxDeliveryAttempts: 30
      eventTimeToLiveInMinutes: 1440
    }
  }
}

output systemTopicName string = systemTopic.name
