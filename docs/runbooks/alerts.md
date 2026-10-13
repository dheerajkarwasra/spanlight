# Run alerts: stuck deliveries, failed deliveries, late evaluation

## Purpose and when to use

Alert rules and budgets are evaluated by the worker once a minute. A change of state (a rule firing or resolving) is written to the database together with one outbox row per notification, and the worker delivers the outbox rows to email, Slack, webhooks and PagerDuty. Use this page when:

- alerts fire in the dashboard but nobody is notified, or notifications arrive late (a stuck outbox);
- a delivery shows `failed` in a channel's delivery log (a dead letter);
- you want a delivery sent again;
- a webhook signing secret leaked or must change;
- rules are not evaluated, or are evaluated late (`alert_evaluation_lag_s` is high);
- you need to stop evaluation, for example during a data migration;
- a PagerDuty incident stays open after the alert resolved.

Delivery is **at least once**. A worker that crashes after the provider accepted a message but before it recorded that fact sends it again when the row's 60-second lease expires. Webhook receivers deduplicate on the `X-Spanlight-Delivery` header and PagerDuty on the `dedup_key`; Slack and email have no such key, so expect a rare duplicate there.

## Prerequisites

- Shell access to the host and the database owner (`postgres` in the Compose stack) for the SQL below.
- For the API calls: a personal access token with the `write` scope, owned by an admin or owner of the organization.
- Docker Compose shortcut:

  ```bash
  spl() { docker compose -f deploy/compose.yaml --env-file deploy/.env "$@"; }
  ```

- `$BASE` is the public address of the dashboard, as in the [overview](README.md#conventions-used-in-these-pages).

## Is it the evaluation or the delivery?

Look at the two halves separately:

```bash
curl -sS "$BASE/health/ready"
```

Healthy: `{"status":"ok","database":"ok","migrations":"ok","worker_heartbeat_age_s":4.2,"outbox_backlog":0,"alert_evaluation_lag_s":31.4,"gateway_mode":"embedded"}`.

- `alert_evaluation_lag_s` is how many seconds ago the newest finished evaluation pass was scheduled. A healthy worker keeps it under about 90. It is `null` before the first pass, whenever `ALERTS_EVALUATION_ENABLED` is off, and once the cleanup job has deleted every finished pass (finished jobs are kept 7 days), so a worker stopped for over a week reads `null`, not a large number. It never makes the probe fail. See [Evaluation lag](#evaluation-lag).
- `outbox_backlog` is the number of pending outbox rows, counted up to 10 000. More than 1 000 rows overdue by 10 minutes make the probe answer `503`. See [A stuck outbox](#a-stuck-outbox).

If an alert shows as firing in the dashboard (**Alerts**, then the events list) but no message arrived, the evaluation worked and the delivery did not. If the rule does not fire although its metric is past the line, the evaluation is the problem.

## A stuck outbox

Outbox rows are in the table `notification_outbox`. A row is `pending` until it is sent (`sent`) or gives up (`failed`, after 8 attempts or at the first permanent error). A worker **claims** a row by adding 1 to its `fence` and setting `lease_until` 60 seconds ahead; it sends only if the row still carries its fence, and settles the row only under the same condition. A row whose lease has expired is claimed again by the next delivery run, so a crashed worker never leaves a row locked for good.

1. Is the worker alive and delivering? The delivery job runs every 30 seconds:

   ```bash
   spl exec postgres psql -U postgres -d spanlight -c \
     "SELECT id, status, outcome, attempts, last_error, now() - created_at AS age
        FROM jobs WHERE kind = 'deliver_notifications' ORDER BY id DESC LIMIT 3"
   ```

   Healthy: the newest rows are `done` with outcome `ok` and under a minute old. No recent rows, or rows that stay `queued`, mean no worker is running: see [Is the worker alive?](README.md#is-the-worker-alive).

2. How many rows are in each state, by kind?

   ```bash
   spl exec postgres psql -U postgres -d spanlight -c \
     "SELECT kind, status, count(*) FROM notification_outbox GROUP BY kind, status ORDER BY kind, status"
   ```

   Expected on a quiet system: a few `sent` rows per kind, no `pending`, and `failed` only for broken channels.

3. Which pending rows are overdue, and who holds them?

   ```bash
   spl exec postgres psql -U postgres -d spanlight -c \
     "SELECT id, kind, attempts, next_attempt_at, lease_until, fence, left(last_error, 80) AS last_error
        FROM notification_outbox
       WHERE status = 'pending' AND next_attempt_at < now()
       ORDER BY next_attempt_at LIMIT 20"
   ```

   Read the columns like this:

   | You see | It means |
   |---|---|
   | `lease_until` in the future | A worker holds the row and is sending it. Wait at most 60 seconds. |
   | `lease_until` in the past, `fence` rising between runs | A worker claims the row and dies or stalls before settling it. Look at the worker log for `outbox_lease_lost` and crashes. |
   | `lease_until` empty, `next_attempt_at` far in the past | No worker is claiming. The worker is down, or the delivery job is failing; see step 1. |
   | `next_attempt_at` in the future | Normal retry backoff (30 s, doubling, up to 1 h). Not overdue, so not listed. |

4. Watch the delivery attempts and their outcomes (`sent`, `retry`, `failed`, `lost`) in Prometheus:

   ```promql
   sum by (kind, outcome) (rate(spanlight_notifications_delivered_total[5m]))
   ```

   `lost` counts attempts that found the lease taken by another worker and sent nothing. A few after a restart are normal; a steady rate means two workers keep stealing rows from each other, usually because one is much slower than its lease. The Grafana dashboard has the same series in its **Alerts** row.

5. To send a row that is waiting in backoff now, make it due:

   ```bash
   spl exec postgres psql -U postgres -d spanlight -c \
     "UPDATE notification_outbox SET next_attempt_at = now()
       WHERE id = '<row-id>' AND status = 'pending'"
   ```

   Expected: `UPDATE 1`. The next delivery run (within 30 seconds) picks it up. Do not clear `lease_until` of a row a live worker holds: the fence stops a stale worker from settling the row, but not from having sent it, so you would cause a duplicate.

Back out: nothing to undo; these steps only read, or make a pending row due earlier.

## Dead letters: failed deliveries

A delivery fails for good on an error a retry cannot fix (a 4xx other than 408 or 429, a redirect, a blocked address, a deleted channel, an unreadable secret) or after 8 attempts. The row is `failed`, keeps its payload, and its `last_error` says why, with no URL, key or address in it.

1. List the recent dead letters:

   ```bash
   spl exec postgres psql -U postgres -d spanlight -c \
     "SELECT id, kind, channel_id, attempts, created_at, last_error
        FROM notification_outbox
       WHERE status = 'failed' AND channel_id IS NOT NULL
       ORDER BY created_at DESC LIMIT 20"
   ```

   Rows with an empty `channel_id` are transactional mail (verification, password reset, invitation, digest). Their payload was cleared when they failed, so that reset links do not linger, and they cannot be retried: ask the person to request the mail again.

2. Fix the cause first, otherwise the retry fails again. The usual ones: the Slack URL or the webhook endpoint changed or was revoked (edit the channel under **Alerts → Channels**), the receiver answers a 4xx, or `CREDENTIALS_KEYS` no longer holds the key the channel's secret was sealed with.

3. Send the channel a test (**Test** on the channel's row) and check it arrives. This also marks the channel verified.

4. Retry the dead letters; see [Re-drive a delivery](#re-drive-a-delivery).

## Re-drive a delivery

A failed delivery is retried with its own id, so a webhook receiver still sees the same `X-Spanlight-Delivery` and deduplicates. Its attempts start over, it is due at once, and its last error stays until the next attempt replaces it.

In the dashboard: **Alerts → Channels**, press **Deliveries** on the channel's row, and press **Retry** on the failed delivery. Only failed rows offer it.

With the API (needs an admin or owner token):

```bash
read -rs TOKEN; echo
curl -sS -H "Authorization: Bearer $TOKEN" \
  "$BASE/api/v1/orgs/<org-id>/alert-channels/<channel-id>/deliveries?status=failed"
curl -sS -X POST -H "Authorization: Bearer $TOKEN" \
  "$BASE/api/v1/orgs/<org-id>/alert-channels/<channel-id>/deliveries/<delivery-id>/retry"
unset TOKEN
```

The first call lists the channel's failed deliveries (newest first, with `id`, `attempts`, `last_error` and a `summary` of the alert). The second answers `200` with the delivery, now `"status":"pending"` and `"attempts":0`. A delivery that is not `failed` answers `409` with the code `NOT_RETRYABLE`; so does an email delivery whose recipient is no longer a verified member of the organization (the check runs again). The retry is recorded in the audit log.

To re-drive many rows at once, retry them through the API in a loop over the list's `id`s. Only if the API is unavailable, use SQL as the database owner, which skips the audit event:

```bash
spl exec postgres psql -U postgres -d spanlight -c \
  "UPDATE notification_outbox
      SET status = 'pending', attempts = 0, next_attempt_at = now(), lease_until = NULL
    WHERE id = '<delivery-id>' AND status = 'failed' AND channel_id IS NOT NULL"
```

Expected: `UPDATE 1`. `UPDATE 0` means the row is not failed, or has no channel.

Verify: within about 30 seconds the row is `sent` (`SELECT status, attempts, last_error FROM notification_outbox WHERE id = '<delivery-id>'`) and the receiver has the message.

## Rotate a webhook signing secret

A webhook is signed with `HMAC-SHA256(secret, "<timestamp>." + body)`, sent as `X-Spanlight-Signature: sha256=<hex>`. The server generates the secret and shows it once. Rotation takes effect at once: rows still waiting in the outbox are signed with the new secret when they are sent, so a receiver that has only the old one rejects them until it is updated. There is no overlap window, so let the receiver accept both secrets while you switch.

1. Make the receiver accept the old and the new secret (add the new one when you have it in step 2, keep the old one until step 4).
2. Rotate. The secret is read into a variable and never printed:

   ```bash
   read -rs TOKEN; echo
   NEW_SECRET=$(curl -sS -X PATCH -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
     -d '{"rotate_secret": true}' \
     "$BASE/api/v1/orgs/<org-id>/alert-channels/<channel-id>" | jq -r .secret)
   unset TOKEN
   ```

   Or press **Rotate secret** on the channel in the dashboard, which shows the secret once. Expected: `NEW_SECRET` holds a non-empty string. If it is `null`, the call failed or the channel is not a webhook (`rotate_secret` applies to webhooks only); run the `curl` without `jq` to read the error. Store the value in the receiver's secret store from the variable, then `unset NEW_SECRET`.
3. Rotating clears the channel's verified mark. Send a test (**Send test**) and check the receiver accepted the signature. The test answers `"status":"sent"`; `failed` carries the reason in `error`.
4. Remove the old secret from the receiver.
5. Look for deliveries that failed in between (a receiver that rejected the signature answers a 4xx, which fails the row for good) and retry them; see [Dead letters](#dead-letters-failed-deliveries).

Back out: rotate again; the previous secret cannot be restored.

## Evaluation lag

The scheduler enqueues one `evaluate_alerts` job a minute (dedupe key `evaluate_alerts:<period start>`), and the worker runs it. A pass stops starting new projects after 40 seconds so that it ends inside the worker's 50-second task limit, and the projects it did not reach go first in the next pass.

1. Read the lag: `curl -sS "$BASE/health/ready" | jq .alert_evaluation_lag_s`. Under about 90 is healthy. `null` means no pass has finished yet, evaluation is off (see below), or the worker has been stopped so long that the cleanup job deleted the finished passes; check `worker_heartbeat_age_s` then.

2. Look at the job rows:

   ```bash
   spl exec postgres psql -U postgres -d spanlight -c \
     "SELECT id, status, outcome, attempts, now() - created_at AS age, left(last_error, 100) AS last_error
        FROM jobs WHERE kind = 'evaluate_alerts' ORDER BY id DESC LIMIT 5"
   ```

   Healthy: a `done` row every minute. Several `queued` rows that grow older mean the worker is down or busy with another job. A `failed` row has its reason in `last_error`. A job has a single attempt: the next minute's job is the retry.

3. Read the worker log for the pass summary and its warnings:

   ```bash
   spl logs --since 10m worker | grep -E 'alerts_evaluated|alert_evaluation_out_of_time|alert_rule_evaluation_failed|alert_project_evaluation_failed'
   ```

   `alerts_evaluated` carries `projects`, `rules`, `transitions` and `errors` of each pass. `alert_evaluation_out_of_time` (with `projects_left`) means a pass ran past 40 seconds and left projects for the next one: the system has more rules than one pass can read, see [Scale Spanlight](scale.md). `alert_rule_evaluation_failed` names the rule and the error type; the rule is skipped for this pass and the others still run.

4. Time a pass by hand. `--dry-run` does the same work and rolls all of it back, so nothing is kept and nothing is queued; without it the pass is real and queues the notifications of every transition:

   ```bash
   spl exec worker spanlight alerts evaluate --dry-run
   ```

   A dry run is one long transaction: it holds the row lock of every rule it evaluates until it ends. While it runs, the worker's own pass skips those rules and an edit, mute or disable of a rule waits. Run it when that is acceptable (a quiet system, or with `ALERTS_EVALUATION_ENABLED` off, restoring it afterwards), or against a restored copy of the database. Logs go to standard error and the result to standard output.

   Expected: `Evaluated <n> rules in <p> projects in <s> s.`, then the counts by outcome (`ok`, `no_data`, `error`) and the transitions. The target is under 10 seconds per 1 000 rules; see [Performance](../performance.md#alert-evaluation). `no_data` is normal for a rule whose window has no calls, and for an anomaly rule that has no baseline yet. The command exits non-zero when a rule or a project errored.

5. The Prometheus series are `spanlight_alert_evaluation_duration_seconds` (histogram), `spanlight_alert_rules_evaluated_total{outcome}` and `spanlight_alert_transitions_total{kind,to_state}`, all from the worker.

## Stop evaluating

Use this while you restore a database, migrate data, or when a bad rule set is overloading the database.

1. In `deploy/.env` set `ALERTS_EVALUATION_ENABLED=false`, then recreate the worker:

   ```bash
   spl up -d worker
   ```

   On Railway set the variable on the `worker` service and redeploy it.

2. Verify: `curl -sS "$BASE/health/ready" | jq .alert_evaluation_lag_s` prints `null`, and after two minutes no new `evaluate_alerts` row appears in the `jobs` table.

While evaluation is off, no rule fires or resolves, no budget changes state, and the weekly digest and the delivery of already queued notifications go on. **A firing `block` budget keeps blocking gateway calls**: the gateway reads the last evaluated state, which no longer changes. To lift a block while evaluation is off, disable or raise the budget in the dashboard (an edit resolves the alert).

To turn evaluation back on (also after a check that needed it off, such as timing a pass), remove the line (or set `true`) and recreate the worker. The first pass compares every rule with its stored state: a rule whose metric changed meanwhile fires or resolves then, and notifies.

## PagerDuty dedup keys

Every alert sent to a PagerDuty channel uses the Events API v2 with the `dedup_key` `spanlight-rule-<rule-id>`: the `trigger` when the rule fires and the `resolve` when it resolves share it, so PagerDuty keeps one incident per firing episode and closes it on the resolve. A channel test uses `spanlight-test-<channel-id>`. Retries and re-driven deliveries repeat the same key, so they never open a second incident.

If an incident stays open after the alert resolved, first check that the resolve was delivered: list the rule's events in the dashboard, and the channel's deliveries for a `failed` row (see [Dead letters](#dead-letters-failed-deliveries)); retrying it resolves the incident. If you need to close the incident without Spanlight, resolve it in PagerDuty, or send the resolve yourself. The routing key is a secret, so read it without echoing it:

```bash
read -rs ROUTING_KEY; echo
curl -sS -X POST https://events.pagerduty.com/v2/enqueue -H 'Content-Type: application/json' \
  -d "{\"routing_key\": \"$ROUTING_KEY\", \"event_action\": \"resolve\", \"dedup_key\": \"spanlight-rule-<rule-id>\"}"
unset ROUTING_KEY
```

Expected: `{"status":"success","message":"Event processed","dedup_key":"spanlight-rule-<rule-id>"}`. The rule id is in the rule's address in the dashboard. A rule that fires again after the incident was resolved opens a new incident with the same key.

## Related

- [Operator runbooks overview](README.md): the first five minutes of any incident, and the worker check.
- [SLO runbook](slo.md): what healthy looks like and the alerts on the worker.
- [Performance](../performance.md#alert-evaluation): the evaluation load test.
