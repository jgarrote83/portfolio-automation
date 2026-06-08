// Event Grid System Topic on the storage account.
//
// Required because Flex Consumption SKU only supports EventGrid-sourced blob
// triggers (polling-based BlobTrigger is not available). See:
//   https://aka.ms/blob-trigger-eg
//
// NOTE: The event subscription (analyzer-on-snapshot) is NOT created here.
// It must be created AFTER the function code is deployed, because the
// `blobs_extension` system key (needed for the webhook URL) only exists once
// the Functions runtime has loaded the extension bundle from the code package.
// The deploy-code.yml workflow handles subscription creation post-deploy.

param location string
param storageAccountName string

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageAccountName
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

output systemTopicName string = systemTopic.name
