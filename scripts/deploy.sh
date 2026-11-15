#!/usr/bin/env bash
# Deploy the agent, the api Lambda and the site, then smoke-test them.
#
#   scripts/deploy.sh             # everything below
#   scripts/deploy.sh --agent     # AgentStack (AgentCore Runtime), upload the index, test a stream
#   scripts/deploy.sh --infra     # ApiStack + FrontendStack (+ AgentStack, which ApiStack needs)
#   scripts/deploy.sh --site      # rebuild + upload the UI only
#
# Needs: AWS credentials for the account, Docker running (the api and agent
# images are built for arm64), Node 20+, the repo's .venv.
# Photos: MEDIA_BASE_URL (e.g. https://<cdn>/media/photos) if set, else the
# AssetsCloudFrontDomain output of a DubaiNestDataStack if the account has one,
# else none (listings show without photos). Nothing account-specific lives here.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REGION="${AWS_REGION:-us-east-1}"
AGENT_STACK=DubaiNestAgentCoreAgentStack
API_STACK=DubaiNestAgentCoreApiStack
SITE_STACK=DubaiNestAgentCoreFrontendStack
MODE="${1:-all}"
# CDK asks before IAM changes; that prompt needs a terminal. REQUIRE_APPROVAL=never
# skips it (e.g. when run from an agent or CI after reviewing `cdk diff`).
APPROVAL="${REQUIRE_APPROVAL:-broadening}"

output() { # stack key
  aws cloudformation describe-stacks --region "$REGION" --stack-name "$1" \
    --query "Stacks[0].Outputs[?OutputKey=='$2'].OutputValue" --output text
}

PY="$ROOT/.venv/bin/python"

if [[ "$MODE" == "all" || "$MODE" == "--agent" ]]; then
  (cd "$ROOT/infra" && npx cdk deploy "$AGENT_STACK" --require-approval "$APPROVAL")
  INDEX_URI="$(output "$AGENT_STACK" IndexUri)"
  INDEX="$ROOT/data/listings_index.npz"
  if [[ -f "$INDEX" ]]; then
    aws s3 cp "$INDEX" "$INDEX_URI" --only-show-errors && echo "✓ index → $INDEX_URI"
  else
    "$PY" "$ROOT/scripts/build_index.py" --source dynamodb --upload "$INDEX_URI"
  fi
  # AgentCore creates the runtime's log group on first use, with no expiry:
  # keep 7 days, like the api Lambda (best effort; it may not exist yet).
  RID="$(output "$AGENT_STACK" AgentRuntimeId)"
  aws logs put-retention-policy --region "$REGION" --retention-in-days 7 \
    --log-group-name "/aws/bedrock-agentcore/runtimes/$RID-DEFAULT" 2>/dev/null || true
  echo "→ test stream from AgentCore"
  "$PY" "$ROOT/scripts/invoke_agent.py" --events "Which DAMAC projects are on the water?" | tail -4
  aws logs put-retention-policy --region "$REGION" --retention-in-days 7 \
    --log-group-name "/aws/bedrock-agentcore/runtimes/$RID-DEFAULT" 2>/dev/null || true
fi

if [[ "$MODE" == "all" || "$MODE" == "--infra" ]]; then
  MEDIA="${MEDIA_BASE_URL:-}"
  if [[ -z "$MEDIA" ]]; then
    ASSETS="$(output DubaiNestDataStack AssetsCloudFrontDomain 2>/dev/null || true)"
    [[ -n "$ASSETS" && "$ASSETS" != "None" ]] && MEDIA="$ASSETS/media/photos"
  fi
  echo "→ photos from ${MEDIA:-(none: set MEDIA_BASE_URL)}"
  (cd "$ROOT/infra" && npx cdk deploy "$API_STACK" "$SITE_STACK" \
    -c mediaBaseUrl="$MEDIA" --require-approval "$APPROVAL")
fi

if [[ "$MODE" == "all" || "$MODE" == "--site" ]]; then
  BUCKET="$(output "$SITE_STACK" SiteBucketName)"
  DIST="$(output "$SITE_STACK" DistributionId)"
  echo "→ building the UI"
  (cd "$ROOT/frontend" && npm run build --silent)
  aws s3 sync "$ROOT/frontend/dist/dubainest-frontend/browser" "s3://$BUCKET" --delete
  aws cloudfront create-invalidation --distribution-id "$DIST" --paths '/*' \
    --query 'Invalidation.Id' --output text
fi

# Live URLs and ids stay out of git: written to .deploy/ (gitignored), not printed
# into any tracked file. Read them with: cat .deploy/outputs.env
mkdir -p "$ROOT/.deploy"
{
  echo "# written by scripts/deploy.sh on $(date -u +%Y-%m-%dT%H:%MZ); gitignored"
  for pair in "$SITE_STACK:CloudFrontDomain:SITE_URL" "$SITE_STACK:DistributionId:DISTRIBUTION_ID" \
              "$AGENT_STACK:AgentRuntimeArn:AGENT_RUNTIME_ARN"; do
    IFS=: read -r stack key name <<<"$pair"
    echo "$name=$(output "$stack" "$key" 2>/dev/null)"
  done
} > "$ROOT/.deploy/outputs.env"

if [[ "$MODE" != "--agent" ]]; then
  "$ROOT/scripts/smoke.sh" "$(output "$SITE_STACK" CloudFrontDomain)"
fi
