// Azure infrastructure for news_digest scheduled pipeline
// Deploys: ACR, Log Analytics, Container Apps Environment, Scheduled Job

@description('Location for all resources')
param location string = 'germanywestcentral'

@description('Container image (push to ACR before deploying)')
param containerImage string = 'simovativedigestacr.azurecr.io/news-digest:v1'

// ── Secret parameters ───────────────────────────────────────────────

@secure()
@description('OpenAI API key')
param openaiApiKey string

@secure()
@description('NewsAPI.org API key')
param newsapiKey string

@secure()
@description('SerpAPI key')
param serpapiKey string

@secure()
@description('SendGrid API key')
param sendGridApiKey string

@secure()
@description('Azure Storage connection string for blob history')
param azureStorageConnectionString string

@secure()
@description('SMTP password')
param smtpPassword string

// ── Non-secret parameters ───────────────────────────────────────────

@description('OpenAI model name')
param openaiModelName string = 'gpt-5.4-mini'

@description('Enable LLM fallback for domain crawler')
param domainCrawlerLlmFallback string = 'true'

@description('SMTP host')
param smtpHost string = 'smtp.gmail.com'

@description('SMTP port')
param smtpPort string = '587'

@description('SMTP user / sender login')
param smtpUser string

@description('Email From address')
param emailFrom string

@description('Comma-separated recipient email addresses')
param emailRecipients string

// ── Cron schedule ───────────────────────────────────────────────────
// Container Apps Jobs use UTC. 08:00 UTC = 10:00 CEST (summer) / 09:00 CET (winter).
// Adjust to '0 9 * * 2,5' if you prefer 10:00 CET in winter (= 11:00 CEST in summer).

@description('Cron expression in UTC (default: Tue+Fri 08:00 UTC = 10:00 CEST)')
param cronExpression string = '0 8 * * 2,5'

// ═══════════════════════════════════════════════════════════════════
// Resources
// ═══════════════════════════════════════════════════════════════════

// ── Azure Container Registry ────────────────────────────────────────

resource acr 'Microsoft.ContainerRegistry/registries@2025-04-01' = {
  name: 'simovativedigestacr'
  location: location
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: true
  }
}

// ── Log Analytics Workspace ─────────────────────────────────────────

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'simovativedigest-logs'
  location: location
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

// ── Container Apps Environment ──────────────────────────────────────

resource environment 'Microsoft.App/managedEnvironments@2025-01-01' = {
  name: 'simovativedigest-env'
  location: location
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

// ── Container Apps Job (scheduled) ──────────────────────────────────

resource job 'Microsoft.App/jobs@2025-01-01' = {
  name: 'news-digest-job'
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    environmentId: environment.id
    configuration: {
      triggerType: 'Schedule'
      scheduleTriggerConfig: {
        cronExpression: cronExpression
        parallelism: 1
        replicaCompletionCount: 1
      }
      replicaTimeout: 3600
      replicaRetryLimit: 1
      registries: [
        {
          server: acr.properties.loginServer
          username: acr.name
          passwordSecretRef: 'acr-password'
        }
      ]
      secrets: [
        {
          name: 'acr-password'
          value: acr.listCredentials().passwords[0].value
        }
        {
          name: 'openai-api-key'
          value: openaiApiKey
        }
        {
          name: 'newsapi-key'
          value: newsapiKey
        }
        {
          name: 'serpapi-key'
          value: serpapiKey
        }
        {
          name: 'sendgrid-api-key'
          value: sendGridApiKey
        }
        {
          name: 'azure-storage-connection-string'
          value: azureStorageConnectionString
        }
        {
          name: 'smtp-password'
          value: smtpPassword
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'news-digest'
          image: containerImage
          resources: {
            cpu: json('2')
            memory: '4Gi'
          }
          env: [
            {
              name: 'OPENAI_API_KEY'
              secretRef: 'openai-api-key'
            }
            {
              name: 'OPENAI_MODEL_NAME'
              value: openaiModelName
            }
            {
              name: 'NEWSAPI_KEY'
              secretRef: 'newsapi-key'
            }
            {
              name: 'SERPAPI_KEY'
              secretRef: 'serpapi-key'
            }
            {
              name: 'DOMAIN_CRAWLER_LLM_FALLBACK'
              value: domainCrawlerLlmFallback
            }
            {
              name: 'SEND_GRID_API_KEY'
              secretRef: 'sendgrid-api-key'
            }
            {
              name: 'AZURE_STORAGE_CONNECTION_STRING'
              secretRef: 'azure-storage-connection-string'
            }
            {
              name: 'SMTP_HOST'
              value: smtpHost
            }
            {
              name: 'SMTP_PORT'
              value: smtpPort
            }
            {
              name: 'SMTP_USER'
              value: smtpUser
            }
            {
              name: 'SMTP_PASSWORD'
              secretRef: 'smtp-password'
            }
            {
              name: 'EMAIL_FROM'
              value: emailFrom
            }
            {
              name: 'EMAIL_RECIPIENTS'
              value: emailRecipients
            }
          ]
        }
      ]
    }
  }
}

// ═══════════════════════════════════════════════════════════════════
// Outputs
// ═══════════════════════════════════════════════════════════════════

output acrLoginServer string = acr.properties.loginServer
output jobName string = job.name
output environmentName string = environment.name
