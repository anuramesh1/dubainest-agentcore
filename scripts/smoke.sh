#!/usr/bin/env bash
# Smoke-test a deployed site: the UI, listings, a property, and a signed chat POST.
#   scripts/smoke.sh https://<distribution>.cloudfront.net
# Read-only: it writes no leads (no enquiry, no viewing confirmation).
set -euo pipefail
URL="${1:?usage: smoke.sh https://<distribution>.cloudfront.net}"
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
body='{"prompt":"hi","history":[]}'
hash=$(printf '%s' "$body" | shasum -a 256 | cut -d' ' -f1)
out=$(curl -sN -X POST "$URL/api/chat" -H 'content-type: application/json' \
  -H "x-amz-content-sha256: $hash" -d "$body")
grep -q '"event_type"' <<<"$out" || fail "POST /api/chat: $out"
echo "✓ POST /api/chat streams: $(grep -o '"event_type": "[a-z]*"' <<<"$out" | tr '\n' ' ')"
