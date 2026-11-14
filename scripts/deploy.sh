#!/usr/bin/env bash
# Deploy the api Lambda + the site (build step 5), then smoke-test it.
#
#   scripts/deploy.sh             # ApiStack + FrontendStack, upload the UI, invalidate
#   scripts/deploy.sh --infra     # stacks only
#   scripts/deploy.sh --site      # rebuild + upload the UI only
#
# Needs: AWS credentials for the account, Docker running (the api image is
# built for arm64), Node 20+. The assets domain for photos is read from the
# shared DubaiNestDataStack (owned by dubainest-ai), so nothing account-specific
# lives in this repo.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REGION="${AWS_REGION:-us-east-1}"
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

if [[ "$MODE" == "all" || "$MODE" == "--infra" ]]; then
  ASSETS="$(output DubaiNestDataStack AssetsCloudFrontDomain)"
  echo "→ photos from $ASSETS/media/photos"
  (cd "$ROOT/infra" && npx cdk deploy "$API_STACK" "$SITE_STACK" \
    -c mediaBaseUrl="$ASSETS/media/photos" --require-approval "$APPROVAL")
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

URL="$(output "$SITE_STACK" CloudFrontDomain)"
"$ROOT/scripts/smoke.sh" "$URL"
