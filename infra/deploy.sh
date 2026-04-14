#!/usr/bin/env bash
# Deploy news_digest pipeline to Azure Container Apps.
#
# Prerequisites:
#   - Azure CLI installed and logged in (az login)
#   - Docker or az acr build available
#   - .env file in the project root with all required variables
#
# Usage:
#   ./infra/deploy.sh              # full deploy (ACR + image + infra)
#   ./infra/deploy.sh --infra-only # skip image build, just update infra
#   ./infra/deploy.sh --image-only # skip infra, just rebuild and push image

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$PROJECT_ROOT/.env"

RESOURCE_GROUP="simovativedigest"
ACR_NAME="simovativedigestacr"
IMAGE_NAME="news-digest"
IMAGE_TAG="v1"

# ── Parse args ───────────────────────────────────────────────────────

SKIP_IMAGE=false
SKIP_INFRA=false

for arg in "$@"; do
  case "$arg" in
    --infra-only) SKIP_IMAGE=true ;;
    --image-only) SKIP_INFRA=true ;;
    *) echo "Unknown arg: $arg"; exit 1 ;;
  esac
done

# ── Load .env ────────────────────────────────────────────────────────
# Cannot use `source .env` because values like AZURE_STORAGE_CONNECTION_STRING
# contain semicolons which bash interprets as command separators.

if [ ! -f "$ENV_FILE" ]; then
  echo "ERROR: .env file not found at $ENV_FILE"
  exit 1
fi

_env_val() {
  # Extract value for a key from .env, handling semicolons and special chars
  grep "^$1=" "$ENV_FILE" | head -1 | cut -d= -f2-
}

OPENAI_API_KEY="$(_env_val OPENAI_API_KEY)"
OPENAI_MODEL_NAME="$(_env_val OPENAI_MODEL_NAME)"
NEWSAPI_KEY="$(_env_val NEWSAPI_KEY)"
SERPAPI_KEY="$(_env_val SERPAPI_KEY)"
SEND_GRID_API_KEY="$(_env_val SEND_GRID_API_KEY)"
AZURE_STORAGE_CONNECTION_STRING="$(_env_val AZURE_STORAGE_CONNECTION_STRING)"
SMTP_HOST="$(_env_val SMTP_HOST)"
SMTP_PORT="$(_env_val SMTP_PORT)"
SMTP_USER="$(_env_val SMTP_USER)"
SMTP_PASSWORD="$(_env_val SMTP_PASSWORD)"
EMAIL_FROM="$(_env_val EMAIL_FROM)"
EMAIL_RECIPIENTS="$(_env_val EMAIL_RECIPIENTS)"
DOMAIN_CRAWLER_LLM_FALLBACK="$(_env_val DOMAIN_CRAWLER_LLM_FALLBACK)"

echo "=== News Digest Azure Deployment ==="
echo "Resource group: $RESOURCE_GROUP"
echo "ACR:            $ACR_NAME"
echo "Image:          $ACR_NAME.azurecr.io/$IMAGE_NAME:$IMAGE_TAG"
echo ""

# ── Step 1: Ensure ACR exists ────────────────────────────────────────

if [ "$SKIP_IMAGE" = false ]; then
  echo "--- Step 1: Ensuring ACR exists ---"
  az acr show --name "$ACR_NAME" --resource-group "$RESOURCE_GROUP" &>/dev/null || \
    az acr create --name "$ACR_NAME" --resource-group "$RESOURCE_GROUP" \
      --sku Basic --admin-enabled true --location germanywestcentral
  echo ""

  # ── Step 2: Build and push image via ACR Tasks ───────────────────
  echo "--- Step 2: Building image in ACR (this may take a few minutes) ---"
  az acr build \
    --registry "$ACR_NAME" \
    --image "$IMAGE_NAME:$IMAGE_TAG" \
    "$PROJECT_ROOT"
  echo ""
fi

# ── Step 3: Deploy Bicep infrastructure ──────────────────────────────

if [ "$SKIP_INFRA" = false ]; then
  echo "--- Step 3: Deploying infrastructure via Bicep ---"
  az deployment group create \
    --resource-group "$RESOURCE_GROUP" \
    --template-file "$SCRIPT_DIR/main.bicep" \
    --parameters \
      containerImage="$ACR_NAME.azurecr.io/$IMAGE_NAME:$IMAGE_TAG" \
      openaiApiKey="$OPENAI_API_KEY" \
      openaiModelName="${OPENAI_MODEL_NAME:-gpt-5.4-mini}" \
      newsapiKey="$NEWSAPI_KEY" \
      serpapiKey="$SERPAPI_KEY" \
      sendGridApiKey="$SEND_GRID_API_KEY" \
      azureStorageConnectionString="$AZURE_STORAGE_CONNECTION_STRING" \
      smtpHost="${SMTP_HOST:-smtp.gmail.com}" \
      smtpPort="${SMTP_PORT:-587}" \
      smtpUser="$SMTP_USER" \
      smtpPassword="$SMTP_PASSWORD" \
      emailFrom="$EMAIL_FROM" \
      emailRecipients="$EMAIL_RECIPIENTS" \
      domainCrawlerLlmFallback="${DOMAIN_CRAWLER_LLM_FALLBACK:-true}"
  echo ""
fi

echo "=== Deployment complete ==="
echo ""
echo "Useful commands:"
echo "  # Manually trigger a test run:"
echo "  az containerapp job start --name news-digest-job --resource-group $RESOURCE_GROUP"
echo ""
echo "  # Check execution history:"
echo "  az containerapp job execution list --name news-digest-job --resource-group $RESOURCE_GROUP -o table"
echo ""
echo "  # View logs (after a run completes):"
echo "  az containerapp job logs show --name news-digest-job --resource-group $RESOURCE_GROUP --follow"
