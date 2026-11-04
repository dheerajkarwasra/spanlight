#!/usr/bin/env bash
# Waits until the load-test database is quiet, so one k6 script does not start while the work of
# the one before it is still running.
#
# k6 gives up on a request after its timeout, but the server keeps running the query, and the
# worker may still be rolling up hours (its job retries after a timeout). Without a pause the next
# scenario would measure the leftovers of the last one, on a database whose cache they have just
# churned.
#
# Quiet means two readings, two seconds apart, with no query of the application role executing
# and no job running. It gives up after SETTLE_TIMEOUT_SECONDS (default 120), says so as a
# workflow warning, and exits 0: a database that never calms down is part of the measurement.
# A poll that fails or takes over 10 seconds counts as "not settled yet" and warns. The script
# always exits 0, because the workflow runs it under `bash -e` and a failure here would skip the
# k6 script that follows.
#
# Needs the same environment as the workflow's other steps: LOAD_ENV_FILE (the Compose env file)
# and, when the stack is not the default project, COMPOSE_PROJECT_NAME and COMPOSE_FILE.
#
#   LOAD_ENV_FILE=deploy/.env backend/load/settle.sh

# No `set -e`: every failure below is handled, and the exit status is always 0.
set -uo pipefail

timeout_seconds=${SETTLE_TIMEOUT_SECONDS:-120}
app_role=${SETTLE_APP_ROLE:-spanlight_app}
if [ -z "${LOAD_ENV_FILE:-}" ]; then
  echo "::warning::settle: LOAD_ENV_FILE is not set; not waiting"
  exit 0
fi

# Runs a command, killing it after 10 seconds. A loop rather than `timeout`, which macOS lacks.
# Like `timeout`, it kills the command's whole process group (`docker compose` runs the compose
# plugin as a child, which would otherwise keep the output open), so call it in a subshell: it
# turns on job control there to give the command a group of its own. `<&0` keeps the caller's
# standard input, which a background command would otherwise not get.
within_10_seconds() {
  set -m
  "$@" <&0 &
  local pid=$! tenths=0
  while kill -0 "$pid" 2>/dev/null; do
    if [ "$tenths" -ge 100 ]; then
      kill -TERM -- "-$pid" 2>/dev/null
      break
    fi
    sleep 0.1
    tenths=$((tenths + 1))
  done
  wait "$pid"
}

# Queries of the application role that are executing right now, plus running jobs whose lease has
# not run out (a crashed worker leaves a running row behind until the lease expires).
busy_count() {
  within_10_seconds docker compose --env-file "$LOAD_ENV_FILE" exec -T postgres \
    psql -U postgres -d spanlight -tA -v "role=$app_role" <<'SQL'
SELECT (SELECT count(*)
          FROM pg_stat_activity
         WHERE datname = 'spanlight'
           AND usename = :'role'
           AND backend_type = 'client backend'
           AND state = 'active')
     + (SELECT count(*) FROM jobs WHERE status = 'running' AND lease_until > now());
SQL
}

started=$SECONDS
quiet_readings=0
while true; do
  if busy=$(busy_count 2>&1) && [[ "$busy" =~ ^[0-9]+$ ]]; then
    if [ "$busy" -eq 0 ]; then
      quiet_readings=$((quiet_readings + 1))
    else
      quiet_readings=0
    fi
  else
    echo "::warning::settle: could not read the database state (${busy:-no output}); not settled yet"
    busy="unknown"
    quiet_readings=0
  fi
  if [ "$quiet_readings" -ge 2 ]; then
    echo "settle: the database is quiet after $((SECONDS - started)) s"
    exit 0
  fi
  if [ $((SECONDS - started)) -ge "$timeout_seconds" ]; then
    echo "::warning::settle: $busy queries or jobs still running after ${timeout_seconds} s; continuing"
    exit 0
  fi
  sleep 2
done
