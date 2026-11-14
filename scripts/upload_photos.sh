#!/usr/bin/env bash
# DubaiNest AI — upload property photos to the assets S3 bucket
#
# The catalogue stores images by filename (see `_media` in
# backend/app/data/mock_properties.py); the API re-anchors them at the CloudFront
# distribution via MEDIA_BASE_URL. For those URLs to resolve, the photo files
# must exist in the assets bucket under object keys equal to their filenames.
#
# Run this AFTER the DataStack is deployed (it creates the bucket + CloudFront)
# and BEFORE/anytime relative to the backend deploy — it only touches S3.
#
# Usage:
#   ./scripts/upload_photos.sh [--dir <path>] [--bucket <name>] [--region <region>] [--delete] [--dry-run]
#
# The bucket is resolved from the DataStack CloudFormation export
# `DubaiNestAssetsBucket` unless --bucket is given.
#
# Prerequisites:
#   - AWS CLI configured with s3:PutObject on the assets bucket
set -euo pipefail

# ── Defaults ──────────────────────────────────────────────────────────────────
PHOTOS_DIR="${LOCAL_PHOTOS_DIR:-$HOME/Desktop/Dubai Photos}"
BUCKET="${ASSETS_BUCKET:-}"
AWS_REGION="${AWS_DEFAULT_REGION:-us-east-1}"
DELETE="false"
DRY_RUN="false"

# ── Parse args ────────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case $1 in
    --dir)     PHOTOS_DIR="$2"; shift 2 ;;
    --bucket)  BUCKET="$2";     shift 2 ;;
    --region)  AWS_REGION="$2"; shift 2 ;;
    --delete)  DELETE="true";   shift ;;
    --dry-run) DRY_RUN="true";  shift ;;
    *)         echo "Unknown arg: $1"; exit 1 ;;
  esac
done

# ── Validate photos dir ───────────────────────────────────────────────────────
if [[ ! -d "$PHOTOS_DIR" ]]; then
  echo "✗ Photos directory not found: $PHOTOS_DIR" >&2
  exit 1
fi

# ── Resolve bucket from CloudFormation if not provided ────────────────────────
if [[ -z "$BUCKET" ]]; then
  echo "→ Resolving assets bucket from DubaiNestDataStack..."
  BUCKET=$(aws cloudformation describe-stacks \
    --stack-name DubaiNestDataStack \
    --query "Stacks[0].Outputs[?ExportName=='DubaiNestAssetsBucket'].OutputValue" \
    --output text \
    --region "$AWS_REGION")
fi

if [[ -z "$BUCKET" || "$BUCKET" == "None" ]]; then
  echo "✗ Could not resolve assets bucket. Deploy DubaiNestDataStack first, or pass --bucket." >&2
  exit 1
fi

echo "=================================================="
echo "  Uploading photos"
echo "    from:   $PHOTOS_DIR"
echo "    to:     s3://$BUCKET"
echo "    region: $AWS_REGION"
echo "    delete: $DELETE   dry-run: $DRY_RUN"
echo "=================================================="

# ── Sync (image files only) ───────────────────────────────────────────────────
# Exclude everything, then re-include known image extensions so stray files
# (e.g. .DS_Store) are never uploaded.
sync_args=(
  "$PHOTOS_DIR" "s3://$BUCKET"
  --region "$AWS_REGION"
  --exclude "*"
  --include "*.png" --include "*.jpg" --include "*.jpeg" --include "*.webp"
)
[[ "$DELETE"  == "true" ]] && sync_args+=(--delete)
[[ "$DRY_RUN" == "true" ]] && sync_args+=(--dryrun)

aws s3 sync "${sync_args[@]}"

echo "✓ Photo upload complete"

# ── Print the CloudFront base for a quick sanity check ────────────────────────
CF_URL=$(aws cloudformation describe-stacks \
  --stack-name DubaiNestDataStack \
  --query "Stacks[0].Outputs[?ExportName=='DubaiNestAssetsCloudFrontUrl'].OutputValue" \
  --output text \
  --region "$AWS_REGION" 2>/dev/null || true)

if [[ -n "$CF_URL" && "$CF_URL" != "None" ]]; then
  echo ""
  echo "CloudFront base (set as backend MEDIA_BASE_URL): $CF_URL"
  echo "Verify, e.g.:"
  echo "  curl -I \"$CF_URL/Bluewaters%20Residences%20%E2%80%94%20Bluewaters%20Island.png\""
fi
