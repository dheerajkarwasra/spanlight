#!/usr/bin/env bash
# Compose smoke test: proves a fresh self-hosted Spanlight stack works end to end.
#
# It builds and starts deploy/compose.yaml on empty volumes, then checks, in order:
#   1. the stack becomes ready (/health/ready: database up, migrations applied)
#   1b. the database role the api connects as is neither a superuser nor able to bypass row-level
#      security (either would silently turn off tenant isolation), and the backup role's URL is
#      not in the api container's environment
#   2. the dashboard sends X-Frame-Options: DENY on /
#   3. the strict Content-Security-Policy of deploy/Caddyfile is the one policy sent on /, on a
#      dashboard route, on an API path and on a health path, and the relaxed policy the API
#      reference needs is sent on /api/docs and nowhere else among them
#   4. the documentation site answers GET /docs/ with 200 and its own policy, which allows
#      page scripts only by hash
#   5. the LLM gateway answers through the web port: a call without a key is refused with 401 in
#      each provider's own error shape (OpenAI on /gw/v1/chat/completions, Anthropic on
#      /gw/v1/messages), which also proves Caddy routes /gw/* to a gateway
#   6. signup, organization, project and API key creation work through the web port with the
#      same session cookie, CSRF header and Origin header the dashboard uses; a second person who
#      belongs to no organization gets 404 NOT_FOUND, not the data, from a project route
#   7. a span sent by the Python SDK shows up (onboarding reports has_traces within 20 s)
#   8. a native POST /v1/traces is accepted (the SDK swallows errors by design, so this is the
#      step that fails on a non-2xx from ingest)
# and always tears the stack down with `down -v`, on success and on failure.
#
# Run it from anywhere:   deploy/smoke.sh
# Exit status: 0 and a final "smoke: ok" line, or non-zero and "smoke: FAILED at step ...".
#
# Environment:
#   SMOKE_ENV_FILE        compose env file, default .local/compose-test.env (a relative path is
#                         resolved from the repository root). It must set every variable that
#                         deploy/.env.example requires: POSTGRES_PASSWORD, APP_DB_PASSWORD and
#                         SECRET_KEY. APP_BASE_URL and WEB_PORT are read from it as well; the
#                         stack is reached on http://localhost:$WEB_PORT, so APP_BASE_URL must be
#                         an http:// URL that matches (it is the Origin the API allows).
#   SMOKE_PROJECT         compose project name, default spanlight-smoke. Keep it different from
#                         a development stack: this script deletes that project's volumes.
#   SMOKE_READY_TIMEOUT   seconds to wait for /health/ready, default 180
#
# Requires: docker with the compose plugin, curl, jq, uv. Secrets and the API key are never
# printed (only a short key prefix); cookies live in a temporary directory removed on exit.
# Written for bash 3.2 (the macOS default), so no bash 4 features.

set -euo pipefail

REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
ENV_FILE=${SMOKE_ENV_FILE:-.local/compose-test.env}
PROJECT=${SMOKE_PROJECT:-spanlight-smoke}
READY_TIMEOUT=${SMOKE_READY_TIMEOUT:-180}
TRACE_TIMEOUT=20

# Set while the script runs; the EXIT trap reads them.
STEP="starting"
STACK_TOUCHED=0
WORK_DIR=""

# Set by prerequisites and the API steps.
BASE_URL=""      # where the stack is reached, http://localhost:$WEB_PORT
ORIGIN=""        # the Origin header value the API allowlists (from APP_BASE_URL)
COOKIE_JAR=""
BODY_FILE=""
RESPONSE_BODY="" # body of the last successful http call
GATEWAY_STATUS="" # HTTP status of the last gateway_refusal call
CSP_VALUE=""     # the Content-Security-Policy of the last fetch_csp call
ORG_ID=""
PROJECT_ID=""
API_KEY=""

# The SDK program run by send_sdk_span. The SDK never raises into user code, so logging is turned
# on to make a rejected batch visible; the has_traces poll is what asserts the span arrived.
SDK_PROGRAM='
import logging
import sys

import spanlight

logging.basicConfig(level=logging.WARNING)
spanlight.init(environment="smoke", release="smoke", max_retries=2)
with spanlight.span("smoke-sdk", kind="chain") as span:
    span.set_output("hello from the sdk")
if not spanlight.flush(timeout=15):
    sys.exit("spanlight.flush() timed out")
'

# The program run inside the api container by check_api_role_cannot_bypass_rls. It connects with
# the container's own DATABASE_URL and prints the attributes of the role it connected as, never
# the URL.
ROLE_PROGRAM='
import os

import psycopg

url = os.environ["DATABASE_URL"].replace("+psycopg", "", 1)
with psycopg.connect(url) as connection:
    row = connection.execute(
        "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
    ).fetchone()
print("rolsuper=%s rolbypassrls=%s" % (str(row[0]).lower(), str(row[1]).lower()))
'

# ---------- helpers ----------

# run_step NAME FUNCTION: print the step line, then run the function. Steps are called plainly
# (never inside `if`, `||` or `$(...)`), so `set -e` stays in force inside them.
run_step() {
  STEP=$1
  printf 'smoke: %s\n' "$STEP"
  "$2"
}

fail() {
  printf 'smoke: %s\n' "$*" >&2
  exit 1
}

compose() {
  docker compose -p "$PROJECT" -f deploy/compose.yaml --env-file "$ENV_FILE" "$@"
}

# env_file_value NAME: the value of NAME in the env file, empty if unset. Plain NAME=value and
# NAME="value" lines are all deploy/.env.example uses; this is not a full dotenv parser.
env_file_value() {
  local value
  value=$(sed -n "s/^$1=//p" "$ENV_FILE" | tail -n 1)
  value=${value#\"}
  value=${value%\"}
  printf '%s' "$value"
}

# config_value NAME DEFAULT: what compose will use for NAME. Like compose, a variable exported in
# the calling shell wins over the env file, and the default applies when neither sets it.
config_value() {
  local name=$1 default=$2 value
  value=${!name:-}
  if [ -z "$value" ]; then
    value=$(env_file_value "$name")
  fi
  printf '%s' "${value:-$default}"
}

# http URL [curl options...]: one HTTP call. The body lands in RESPONSE_BODY. Fails, and prints
# the error body, unless the final status is 2xx. --fail-with-body covers 4xx and 5xx; the
# status check also catches a 3xx, which curl would otherwise report as success.
http() {
  local status="" curl_status=0
  : >"$BODY_FILE"
  status=$(curl --fail-with-body --silent --show-error \
    --connect-timeout 5 --max-time 30 \
    --output "$BODY_FILE" --write-out '%{http_code}' "$@") || curl_status=$?
  if [ "$curl_status" -ne 0 ] || [ "${status:0:1}" != "2" ]; then
    printf 'smoke: request to %s failed (curl exit %s, HTTP %s)\n' \
      "$1" "$curl_status" "${status:-none}" >&2
    cat "$BODY_FILE" >&2
    printf '\n' >&2
    return 1
  fi
  RESPONSE_BODY=$(<"$BODY_FILE")
}

# csrf_token: the spl_csrf cookie value from the cookie jar (columns: domain, subdomains, path,
# secure, expiry, name, value).
csrf_token() {
  awk -F '\t' '$6 == "spl_csrf" { print $7 }' "$COOKIE_JAR"
}

# api_post PATH JSON: a signed-in POST through the web port, the way the dashboard makes it.
api_post() {
  local path=$1 body=$2 csrf
  csrf=$(csrf_token)
  [ -n "$csrf" ] || fail "the cookie jar has no spl_csrf cookie after signup"
  http "$BASE_URL$path" --request POST \
    --cookie "$COOKIE_JAR" --cookie-jar "$COOKIE_JAR" \
    --header "Origin: $ORIGIN" --header "X-CSRF-Token: $csrf" \
    --header 'Content-Type: application/json' --data @- <<<"$body"
}

# caddyfile_csp MATCHER: the Content-Security-Policy value deploy/Caddyfile sets for the named
# matcher (`header @MATCHER Content-Security-Policy "..."`), empty if there is no such line.
caddyfile_csp() {
  sed -nE "s/^[[:space:]]*header @$1 Content-Security-Policy \"(.*)\"[[:space:]]*\$/\\1/p" \
    deploy/Caddyfile
}

# fetch_csp PATH: GET PATH through the web port and put its Content-Security-Policy in
# CSP_VALUE. Fails unless the response carries exactly one such header: a second one would be
# enforced as well, so a relaxed policy could not hide behind a strict one or the reverse.
fetch_csp() {
  local path=$1 count
  http "$BASE_URL$path" --dump-header "$WORK_DIR/headers"
  # Header names are case-insensitive; every header line ends in a CR, removed here.
  count=$(grep -ciE '^content-security-policy:' "$WORK_DIR/headers" || true)
  [ "$count" = "1" ] ||
    fail "$path sends ${count:-0} Content-Security-Policy headers, expected exactly 1"
  CSP_VALUE=$(awk 'tolower($0) ~ /^content-security-policy:/ {
      sub(/^[^:]*:[ \t]*/, ""); sub(/[ \t\r]*$/, ""); print }' "$WORK_DIR/headers")
}

# random_hex BYTES: 2 * BYTES lowercase hex characters from the system random source.
random_hex() {
  head -c "$1" /dev/urandom | od -An -tx1 | tr -d ' \n'
}

show_logs() {
  printf 'smoke: last log lines of every service follow\n' >&2
  compose logs --no-color --tail 40 >&2
}

# The EXIT trap: report, tear the stack down, and exit with the original status. Teardown runs
# whatever happened above and can only turn a pass into a failure, never hide a failure.
cleanup() {
  local status=$?
  trap - EXIT INT TERM
  set +e
  if [ "$status" -ne 0 ] && [ "$STACK_TOUCHED" -eq 1 ]; then
    show_logs
  fi
  if [ "$STACK_TOUCHED" -eq 1 ]; then
    printf 'smoke: tearing down (docker compose down -v)\n'
    if ! compose down --volumes --remove-orphans; then
      printf 'smoke: teardown failed; remove project %s by hand\n' "$PROJECT" >&2
      [ "$status" -ne 0 ] || status=1
    fi
  fi
  if [ -n "$WORK_DIR" ]; then
    rm -rf "$WORK_DIR"
  fi
  if [ "$status" -eq 0 ]; then
    printf 'smoke: ok\n'
  else
    printf 'smoke: FAILED at step "%s" (exit %s)\n' "$STEP" "$status" >&2
  fi
  exit "$status"
}

# ---------- steps ----------

check_prerequisites() {
  local tool name
  for tool in docker curl jq uv; do
    command -v "$tool" >/dev/null 2>&1 || fail "$tool is required but was not found on PATH"
  done
  docker compose version >/dev/null 2>&1 || fail "the docker compose plugin is required"
  docker info >/dev/null 2>&1 || fail "the docker daemon is not reachable"

  [ -f "$ENV_FILE" ] || fail "env file $ENV_FILE not found (set SMOKE_ENV_FILE)"
  for name in POSTGRES_PASSWORD APP_DB_PASSWORD SECRET_KEY; do
    [ -n "$(config_value "$name" "")" ] || fail "$name is not set in $ENV_FILE"
  done

  local web_port app_base_url
  web_port=$(config_value WEB_PORT 8080)
  app_base_url=$(config_value APP_BASE_URL http://localhost:8080)
  case $web_port in
    '' | *[!0-9]*) fail "WEB_PORT must be a number" ;;
  esac
  case $app_base_url in
    http://*) ;;
    *) fail "APP_BASE_URL must be an http:// URL (cookies are Secure over https)" ;;
  esac
  BASE_URL="http://localhost:$web_port"
  ORIGIN=$(printf '%s' "$app_base_url" | sed -E 's#^(https?://[^/]+).*#\1#')

  WORK_DIR=$(mktemp -d "${TMPDIR:-/tmp}/spanlight-smoke.XXXXXX")
  COOKIE_JAR="$WORK_DIR/cookies.txt"
  BODY_FILE="$WORK_DIR/body"
}

start_stack() {
  STACK_TOUCHED=1
  # A run killed hard (SIGKILL) can leave this project's volume behind with an old database
  # password in it, so always begin from nothing.
  compose down --volumes --remove-orphans
  compose up -d --build
}

wait_for_ready() {
  local deadline=$((SECONDS + READY_TIMEOUT))
  until http "$BASE_URL/health/ready" 2>/dev/null; do
    if [ "$SECONDS" -ge "$deadline" ]; then
      http "$BASE_URL/health/ready" || true # once more, with the error shown
      fail "the stack was not ready within ${READY_TIMEOUT}s"
    fi
    sleep 2
  done
  jq -e '.status == "ok" and .database == "ok" and .migrations == "ok"' \
    <<<"$RESPONSE_BODY" >/dev/null || fail "/health/ready is not fully ok: $RESPONSE_BODY"
}

# Row-level security only isolates tenants when the api's role is subject to it: a superuser or a
# BYPASSRLS role ignores every policy without any error. compose gives the api the unprivileged
# app role and only the worker the backup role; this fails if that ever changes. It also checks
# that the backup role's URL (BACKUP_DATABASE_URL, which bypasses row-level security) is not in
# the api container's environment at all.
check_api_role_cannot_bypass_rls() {
  local attributes
  attributes=$(compose exec -T api python -c "$ROLE_PROGRAM") ||
    fail "could not read the api's database role from the api container"
  [ "$attributes" = "rolsuper=false rolbypassrls=false" ] ||
    fail "the api's database role is not the unprivileged app role: $attributes"
  compose exec -T api sh -c 'test -z "${BACKUP_DATABASE_URL:-}"' ||
    fail "BACKUP_DATABASE_URL is set in the api container; only the worker may have the backup role"
}

check_security_headers() {
  http "$BASE_URL/" --dump-header "$WORK_DIR/headers"
  # Header names are case-insensitive, and every header line ends in a CR, which [[:space:]]
  # absorbs.
  grep -qiE '^x-frame-options:[[:space:]]*DENY[[:space:]]*$' "$WORK_DIR/headers" ||
    fail "/ does not send X-Frame-Options: DENY"
}

# The strict policy covers everything except the API reference, which FastAPI builds with an
# inline script and a bundle from jsDelivr. Each response must carry the policy deploy/Caddyfile
# gives it, so a matcher that stops matching (or matches too much) fails here. The strict one is
# also checked for its own properties, so editing the Caddyfile cannot weaken it unnoticed.
check_content_security_policy() {
  local strict relaxed path
  strict=$(caddyfile_csp notApiDocs)
  relaxed=$(caddyfile_csp apiDocs)
  [ -n "$strict" ] || fail "deploy/Caddyfile has no strict policy (header @notApiDocs ...)"
  [ -n "$relaxed" ] || fail "deploy/Caddyfile has no API reference policy (header @apiDocs ...)"
  [ "$strict" != "$relaxed" ] || fail "deploy/Caddyfile gives /api/docs the strict policy"

  # No script-src directive: scripts fall back to default-src 'self', so no inline script, no
  # eval and no other origin can run on the dashboard.
  case "; $strict;" in
    *"; default-src 'self';"*) ;;
    *) fail "the strict policy does not start from default-src 'self': $strict" ;;
  esac
  case $strict in
    *script-src* | *unsafe-eval* | *jsdelivr*)
      fail "the strict policy allows more scripts than default-src 'self': $strict" ;;
  esac
  case $strict in
    *"frame-ancestors 'none'"*) ;;
    *) fail "the strict policy allows framing: $strict" ;;
  esac

  for path in / /login /api/openapi.json /health/live; do
    fetch_csp "$path"
    [ "$CSP_VALUE" = "$strict" ] ||
      fail "$path does not send the strict policy of deploy/Caddyfile, it sends: $CSP_VALUE"
  done

  fetch_csp /api/docs
  [ "$CSP_VALUE" = "$relaxed" ] ||
    fail "/api/docs does not send the API reference policy of deploy/Caddyfile, it sends: $CSP_VALUE"
}

# The documentation site is static files in the web image, so this needs no API. Its pages carry
# a few inline scripts, which the policy allows by SHA-256 hash (generated at image build time);
# a script-src with 'unsafe-inline' or an outside origin would defeat that.
check_docs_site() {
  local script_src
  http "$BASE_URL/docs/" --dump-header "$WORK_DIR/headers"
  grep -qE '^HTTP/[0-9.]+ 200' "$WORK_DIR/headers" || fail "GET /docs/ is not 200"
  grep -q 'Spanlight' <<<"$RESPONSE_BODY" || fail "GET /docs/ did not return the documentation"
  fetch_csp /docs/
  case "; $CSP_VALUE;" in
    *"; default-src 'self';"*) ;;
    *) fail "/docs/ does not start from default-src 'self': $CSP_VALUE" ;;
  esac
  case $CSP_VALUE in
    *"script-src 'self'"*"'sha256-"*) ;;
    *) fail "/docs/ allows no hashed inline scripts (docs-csp.caddy missing or empty): $CSP_VALUE" ;;
  esac
  script_src=${CSP_VALUE#*script-src }
  script_src=${script_src%%;*}
  case $script_src in
    *"'unsafe-inline'"* | *"'unsafe-eval'"* | *"http://"* | *"https://"*) fail "the /docs/ script-src is too permissive: $script_src" ;;
  esac
}

# gateway_refusal PATH: POST an empty chat body to PATH on the gateway without any key and put the
# status in GATEWAY_STATUS and the body in RESPONSE_BODY. `http` cannot be used: it treats every
# non-2xx as a failure and here 401 is the answer being asked for.
gateway_refusal() {
  local curl_status=0
  : >"$BODY_FILE"
  GATEWAY_STATUS=$(curl --silent --show-error --connect-timeout 5 --max-time 30 \
    --output "$BODY_FILE" --write-out '%{http_code}' \
    --request POST --header 'Content-Type: application/json' --data '{}' \
    "$BASE_URL$1") || curl_status=$?
  [ "$curl_status" -eq 0 ] || fail "request to $1 failed (curl exit $curl_status)"
  RESPONSE_BODY=$(<"$BODY_FILE")
}

# With no key the gateway refuses before it reads the body or touches a provider, so this needs no
# credential and costs nothing. Each surface answers in the shape its official SDK expects.
check_gateway_refuses_without_key() {
  gateway_refusal /gw/v1/chat/completions
  [ "$GATEWAY_STATUS" = "401" ] ||
    fail "POST /gw/v1/chat/completions without a key is HTTP $GATEWAY_STATUS, expected 401: $RESPONSE_BODY"
  jq -e '.error.type == "invalid_request_error"' <<<"$RESPONSE_BODY" >/dev/null ||
    fail "the OpenAI-shaped refusal has no error.type invalid_request_error: $RESPONSE_BODY"

  gateway_refusal /gw/v1/messages
  [ "$GATEWAY_STATUS" = "401" ] ||
    fail "POST /gw/v1/messages without a key is HTTP $GATEWAY_STATUS, expected 401: $RESPONSE_BODY"
  jq -e '.type == "error"' <<<"$RESPONSE_BODY" >/dev/null ||
    fail "the Anthropic-shaped refusal has no top-level type error: $RESPONSE_BODY"
}

# sign_up [COOKIE_JAR]: a new account, signed in with the cookies stored in the jar (default: the
# main one).
sign_up() {
  local jar=${1:-$COOKIE_JAR} email password body
  email="smoke-$(random_hex 4)@example.com"
  password="smoke-$(random_hex 12)"
  body=$(jq -n --arg email "$email" --arg password "$password" \
    '{email: $email, password: $password, name: "Smoke Test"}')
  # No session yet, so no CSRF token: the Origin check is the guard on signup.
  http "$BASE_URL/api/v1/auth/signup" --request POST \
    --cookie "$jar" --cookie-jar "$jar" \
    --header "Origin: $ORIGIN" --header 'Content-Type: application/json' --data @- <<<"$body"
}

create_organization() {
  api_post /api/v1/orgs '{"name": "Smoke Org"}'
  ORG_ID=$(jq -er '.id' <<<"$RESPONSE_BODY") || fail "the organization response has no id"
}

create_project() {
  api_post "/api/v1/orgs/$ORG_ID/projects" '{"name": "Smoke Project"}'
  PROJECT_ID=$(jq -er '.id' <<<"$RESPONSE_BODY") || fail "the project response has no id"
}

# A person who signed up but belongs to no organization must be told the project does not exist
# (404), never that it exists but is off limits (403) or its data (200). A Phase 5 route
# (datasets) stands for the whole project API; the member's own call is the control.
check_non_member_gets_404() {
  local jar="$WORK_DIR/outsider-cookies.txt" status="" curl_status=0
  http "$BASE_URL/api/v1/projects/$PROJECT_ID/datasets" --cookie "$COOKIE_JAR"
  sign_up "$jar"
  : >"$BODY_FILE"
  status=$(curl --silent --show-error --connect-timeout 5 --max-time 30 \
    --output "$BODY_FILE" --write-out '%{http_code}' --cookie "$jar" \
    "$BASE_URL/api/v1/projects/$PROJECT_ID/datasets") || curl_status=$?
  [ "$curl_status" -eq 0 ] || fail "the non-member's request failed (curl exit $curl_status)"
  [ "$status" = "404" ] ||
    fail "GET /api/v1/projects/<id>/datasets as a non-member is HTTP $status, expected 404"
  jq -e '.code == "NOT_FOUND"' "$BODY_FILE" >/dev/null ||
    fail "the non-member's 404 does not carry the code NOT_FOUND: $(head -c 300 "$BODY_FILE")"
}

create_api_key() {
  api_post "/api/v1/projects/$PROJECT_ID/keys" '{"name": "smoke"}'
  API_KEY=$(jq -er '.secret' <<<"$RESPONSE_BODY") || fail "the key response has no secret"
  # curl reads the key from this file, so it never appears in a process list.
  printf 'Authorization: Bearer %s\n' "$API_KEY" >"$WORK_DIR/auth-header"
  RESPONSE_BODY="" # the body held the key
  printf 'smoke: created API key %s...\n' "${API_KEY:0:13}"
}

send_sdk_span() {
  SPANLIGHT_API_KEY=$API_KEY SPANLIGHT_HOST=$BASE_URL \
    uv run --frozen --no-dev --directory sdks/python python -c "$SDK_PROGRAM"
}

wait_for_first_trace() {
  local deadline=$((SECONDS + TRACE_TIMEOUT))
  while :; do
    http "$BASE_URL/api/v1/projects/$PROJECT_ID/onboarding" --cookie "$COOKIE_JAR"
    if jq -e '.has_traces == true' <<<"$RESPONSE_BODY" >/dev/null; then
      return 0
    fi
    [ "$SECONDS" -lt "$deadline" ] || fail "no trace after ${TRACE_TIMEOUT}s: $RESPONSE_BODY"
    sleep 1
  done
}

send_native_span() {
  local now body
  now=$(date -u +%Y-%m-%dT%H:%M:%S+00:00)
  body=$(jq -n --arg trace_id "$(random_hex 16)" --arg span_id "$(random_hex 8)" --arg now "$now" \
    '{spans: [{trace_id: $trace_id, span_id: $span_id, name: "smoke-native",
               kind: "chain", status: "ok", start_time: $now, end_time: $now}]}')
  http "$BASE_URL/v1/traces" --request POST \
    --header "@$WORK_DIR/auth-header" --header 'Content-Type: application/json' --data @- <<<"$body"
  jq -e '.accepted == 1 and (.rejected | length) == 0' <<<"$RESPONSE_BODY" >/dev/null ||
    fail "ingest did not accept the span: $RESPONSE_BODY"
}

main() {
  trap cleanup EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  cd "$REPO_ROOT"

  run_step "check prerequisites" check_prerequisites
  run_step "start the stack on fresh volumes" start_stack
  run_step "wait for /health/ready" wait_for_ready
  run_step "check the api database role cannot bypass row-level security" \
    check_api_role_cannot_bypass_rls
  run_step "check security headers on /" check_security_headers
  run_step "check the Content-Security-Policy on / and /api/docs" check_content_security_policy
  run_step "check the documentation site at /docs/" check_docs_site
  run_step "check the gateway refuses a call without a key" check_gateway_refuses_without_key
  run_step "sign up" sign_up
  run_step "create an organization" create_organization
  run_step "create a project" create_project
  run_step "check a non-member gets 404 from a project route" check_non_member_gets_404
  run_step "create an API key" create_api_key
  run_step "send a span with the Python SDK" send_sdk_span
  run_step "wait for the trace (has_traces)" wait_for_first_trace
  run_step "send a span to POST /v1/traces" send_native_span
}

main "$@"
