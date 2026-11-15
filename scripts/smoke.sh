#!/usr/bin/env bash
# Smoke-test a deployed site: the UI, listings, a property, and a real chat answer
# streamed from AgentCore through the Lambda relay.
#   scripts/smoke.sh [https://<distribution>.cloudfront.net]   (default: .deploy/outputs.env)
# Writes no leads (no enquiry, no viewing confirmation). The chat question costs
# about $0.007 and counts towards the daily question cap.
set -euo pipefail
# The URL comes from the argument, else .deploy/outputs.env (gitignored).
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
URL="${1:-$(grep -s '^SITE_URL=' "$ROOT/.deploy/outputs.env" | cut -d= -f2-)}"
[[ -n "$URL" ]] || { echo "usage: smoke.sh https://<distribution>.cloudfront.net"; exit 1; }
fail() { echo "✗ $*"; exit 1; }

code=$(curl -s -o /dev/null -w '%{http_code}' "$URL/")
[[ "$code" == 200 ]] || fail "UI: HTTP $code"; echo "✓ UI 200"

n=$(curl -sf "$URL/api/properties" | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))') \
  || fail "GET /api/properties"
[[ "$n" -gt 0 ]] || fail "no listings"; echo "✓ /api/properties → $n listings"

curl -sf "$URL/api/properties/1" >/dev/null || fail "GET /api/properties/1"; echo "✓ /api/properties/1"
curl -sf "$URL/api/chat/property-intro/1" >/dev/null || fail "property-intro"; echo "✓ property-intro"

# API errors must reach the browser as JSON, not as the SPA's index.html.
code=$(curl -s -o /dev/null -w '%{http_code}' "$URL/api/properties/does-not-exist")
[[ "$code" == 404 ]] || fail "unknown property: HTTP $code (want 404)"; echo "✓ unknown property → 404"
code=$(curl -s -o /dev/null -w '%{http_code}' "$URL/properties/1")
[[ "$code" == 200 ]] || fail "deep link /properties/1: HTTP $code"; echo "✓ deep link → index.html"
code=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$URL/api/chat" -H 'content-type: application/json' -d '{"prompt":"hi"}')
[[ "$code" == 403 ]] || fail "unsigned POST: HTTP $code (want 403)"; echo "✓ POST without body hash → 403"

# POST through CloudFront to the IAM Function URL: needs the body's SHA-256.
sid="smoke-$(uuidgen | tr 'A-Z' 'a-z')"
body="{\"prompt\":\"What is the price of Burj Vista?\",\"history\":[],\"session_id\":\"$sid\"}"
hash=$(printf '%s' "$body" | shasum -a 256 | cut -d' ' -f1)
start=$(date +%s)
out=$(curl -sN --max-time 90 -X POST "$URL/api/chat" -H 'content-type: application/json' \
  -H "x-amz-content-sha256: $hash" -d "$body")
kinds=$(grep -o '"event_type": "[a-z]*"' <<<"$out" | cut -d'"' -f4 | tr '\n' ' ')
[[ "$kinds" == status* && "$kinds" == *"chunk"* && "$kinds" == *"done " ]] \
  || fail "POST /api/chat: events [$kinds] ${out:0:300}"
grep -q '1,850,000' <<<"$out" || fail "chat answer is missing the Burj Vista price"
echo "✓ POST /api/chat → AgentCore answered in $(( $(date +%s) - start ))s: [$kinds]"
