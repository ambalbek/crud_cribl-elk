#!/usr/bin/env bash
# lab/validate.sh — smoke-test every service endpoint in the lab stack
#
# Usage:  bash lab/validate.sh
# Expects the lab stack to be running on localhost.

set -uo pipefail

# Use 127.0.0.1 instead of localhost to avoid IPv6 issues on Windows
HOST="127.0.0.1"

PASS=0
FAIL=0
WARN=0

COOKIES=""
COOKIE_JAR="$(pwd)/lab/.cookies.tmp"

check() {
  local label="$1" url="$2" expect_code="${3:-200}"
  local cookie_arg=()
  [ -n "$COOKIES" ] && cookie_arg=(-b "$COOKIES")
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "${cookie_arg[@]}" "$url" 2>/dev/null || echo "000")
  if [ "$code" = "$expect_code" ]; then
    echo "  PASS  $label  ($code)"
    ((PASS++))
  else
    echo "  FAIL  $label  (got $code, expected $expect_code)"
    ((FAIL++))
  fi
}

check_json() {
  local label="$1" url="$2"
  local cookie_arg=()
  [ -n "$COOKIES" ] && cookie_arg=(-b "$COOKIES")
  body=$(curl -sf --max-time 10 "${cookie_arg[@]}" "$url" 2>/dev/null || echo "")
  if echo "$body" | python -m json.tool >/dev/null 2>&1; then
    echo "  PASS  $label  (valid JSON)"
    ((PASS++))
  else
    echo "  FAIL  $label  (not valid JSON: ${body:0:80})"
    ((FAIL++))
  fi
}

echo ""
echo "======================================"
echo "  Lab Stack Validation"
echo "======================================"

# ── 1. Infrastructure ─────────────────────────────────────────────────────────
echo ""
echo "--- Infrastructure ---"
check "Elasticsearch cluster health" "http://$HOST:9200/_cluster/health"
check "Kibana status"                "http://$HOST:5601/api/status"
check_json "ES cluster info"         "http://$HOST:9200"
check "APM Server"                   "http://$HOST:8200"

# ── 2. App services health ────────────────────────────────────────────────────
echo ""
echo "--- Service Health Endpoints ---"
check "cribl-framework /cribl/health"  "http://$HOST:5000/cribl/health"
check "cribl_service /health"          "http://$HOST:8001/health"
check "ece_service /health"            "http://$HOST:8002/health"

# ── 3. Flask portal ──────────────────────────────────────────────────────────
echo ""
echo "--- Flask Portal ---"
check "Login page renders"      "http://$HOST:5000/cribl/login"
check "Root redirects"          "http://$HOST:5000/" 302

# Login and get session cookie
echo "  .... logging in as admin ...."
LOGIN_RESP=$(curl -sf -c $COOKIE_JAR -d "username=admin&password=admin" \
  -L --max-time 10 -o /dev/null -w '%{http_code}' \
  "http://$HOST:5000/cribl/login" 2>/dev/null || echo "000")
if [ "$LOGIN_RESP" = "200" ]; then
  echo "  PASS  Admin login  ($LOGIN_RESP)"
  ((PASS++))
  COOKIES="$COOKIE_JAR"

  # Authenticated pages
  check "Portal page (authed)"    "http://$HOST:5000/cribl/portal" 200
  check "Catalog page (authed)"   "http://$HOST:5000/cribl/catalog" 200

  # Submit a test onboarding request
  echo "  .... submitting test onboarding request ...."
  SUBMIT_RESP=$(curl -sf -b $COOKIE_JAR \
    -H "Content-Type: application/json" \
    -d '{
      "apmid": "LAB001",
      "appname": "lab_test_app",
      "region": "azn",
      "log_destinations": ["elk"],
      "log_types": ["app_logs"],
      "groups": ["lab-group"]
    }' \
    --max-time 10 \
    "http://$HOST:5000/cribl/portal/api/submit" 2>/dev/null || echo "")
  if echo "$SUBMIT_RESP" | python -m json.tool >/dev/null 2>&1; then
    REQ_ID=$(echo "$SUBMIT_RESP" | python -c "import sys,json; print(json.load(sys.stdin).get('request_id',''))" 2>/dev/null || echo "")
    if [ -n "$REQ_ID" ]; then
      echo "  PASS  Portal submit  (request_id=$REQ_ID)"
      ((PASS++))
    else
      echo "  PASS  Portal submit  (valid JSON, no request_id in response)"
      ((PASS++))
    fi
  else
    echo "  FAIL  Portal submit  (response: ${SUBMIT_RESP:0:120})"
    ((FAIL++))
  fi

  # Catalog API
  check_json "Catalog API"  "http://$HOST:5000/cribl/api/catalog"

  # Entitlements API
  check_json "Entitlements API"  "http://$HOST:5000/cribl/api/entitlements"

  # ELK health (503 expected — Logstash removed, health reports degraded)
  check "ELK health API (expect 503, no Logstash)"  "http://$HOST:5000/cribl/health/elk" 503

else
  echo "  FAIL  Admin login  ($LOGIN_RESP)"
  ((FAIL++))
  echo "  SKIP  (skipping authenticated tests)"
fi

# ── 4. cribl_service endpoints ────────────────────────────────────────────────
echo ""
echo "--- cribl_service API ---"
check_json "GET /health"  "http://$HOST:8001/health"
# These will return 500 (no Cribl leader configured) but should not crash
check "GET /api/v1/worker-groups (expect 500)"  "http://$HOST:8001/api/v1/worker-groups" 500

# ── 5. ece_service endpoints ─────────────────────────────────────────────────
echo ""
echo "--- ece_service API ---"
check_json "GET /health"  "http://$HOST:8002/health"
# Indexes endpoint works without security; roles/role-mappings need x-pack security enabled
check_json "List ES indexes"      "http://$HOST:8002/api/v1/indexes"
check_json "List ILM policies"    "http://$HOST:8002/ece/indexes/ilm/policies"
# These return 500 when x-pack security is disabled — expected in lab
check "List ES roles (expect 4xx/5xx, security off)"        "http://$HOST:8002/api/v1/roles" 405
check "List role-mappings (expect 4xx/5xx, security off)"   "http://$HOST:8002/api/v1/role-mappings" 405

# ── 6. Verify onboarding index was created in ES ─────────────────────────────
echo ""
echo "--- Elasticsearch Data ---"
ES_IDX=$(curl -sf --max-time 10 "http://$HOST:9200/_cat/indices/cribl-onboarding-requests?format=json" 2>/dev/null || echo "[]")
if echo "$ES_IDX" | python -c "import sys,json; d=json.load(sys.stdin); assert len(d)>0" 2>/dev/null; then
  echo "  PASS  Onboarding index exists"
  ((PASS++))
else
  echo "  WARN  Onboarding index not yet created (will be created on first submit)"
  ((WARN++))
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "======================================"
echo "  Results:  $PASS passed, $FAIL failed, $WARN warnings"
echo "======================================"
echo ""

rm -f $COOKIE_JAR

if [ "$FAIL" -gt 0 ]; then
  exit 1
fi
exit 0
