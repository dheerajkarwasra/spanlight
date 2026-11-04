# Respond to a personal data breach

## Purpose and when to use

Use this runbook when personal data held by a Spanlight instance may have been accessed, disclosed, changed, lost or destroyed without authorization. Examples: a leaked database backup or dump, a stolen `SECRET_KEY` or `CREDENTIALS_KEYS` value, an API key or session used by someone other than its owner, a tenant seeing another tenant's traces, a public bucket of trace exports, or a vulnerability report that shows real data was reached.

A Spanlight instance holds two kinds of personal data, and the operator has a different role for each under the GDPR:

| Data | Examples | Operator's role | Who must be told |
|---|---|---|---|
| Account data | users' emails and names, sessions, login attempts, audit log entries | **Controller** | The supervisory authority (Art. 33) and, at high risk, the people affected (Art. 34) |
| Customer content on a hosted service | prompts, completions, metadata, end-user and session ids in traces | **Processor** for each customer organization, under the data processing agreement | Each affected customer, the controller (Art. 33(2)); they notify their authority and their own users |

On a self-hosted instance you are the controller for both rows: your traces hold your own end users' data. Treat trace content like account data; Art. 33 and 34 apply to it directly. This runbook gives the legal clock and the order of work; it is not legal advice. Involve a lawyer as soon as a breach looks notifiable.

## The clocks

The clocks start when you become **aware**: when you are reasonably certain that a security incident has affected personal data. A suspicion you are still checking does not start them, but the check must be quick; do not delay it to delay the clock.

| Duty | Deadline | Source |
|---|---|---|
| Notify the supervisory authority, unless the breach is unlikely to result in a risk to people's rights and freedoms | Without undue delay and, where feasible, **within 72 hours** of awareness. A later notification must state the reasons for the delay. | Art. 33(1) |
| As processor, notify each affected customer | Without undue delay, **aiming for 48 hours** after awareness, with the Art. 33(3) information (the hosted service's data processing agreement promises this) | Art. 33(2), DPA |
| Notify the people affected, when the breach is likely to result in a **high** risk to them | Without undue delay | Art. 34(1) |
| Record the breach in the internal breach register, whether or not it was notified | Always | Art. 33(5) |

Information you do not have yet may follow in phases (Art. 33(4)): send what you know inside the deadline and complete it later.

**Which authority.** The supervisory authority competent for the operator. For the hosted service, the privacy policy names it. A self-hoster notifies the authority competent for their own organization.

## Prerequisites

- Shell access to the host, the database owner (`postgres` in the Compose stack) and, on Railway, the CLI linked to the project.
- The incident log: one document, started now, where every step below is written down with its UTC time and who did it. It becomes the breach register entry.
- Docker Compose shortcut:

  ```bash
  spl() { docker compose -f deploy/compose.yaml --env-file deploy/.env "$@"; }
  ```

## Steps

### 1. Detect and preserve evidence

Write down, separately, when and how the incident was noticed and by whom, and when you became aware that personal data was affected. Counting the clocks from the time it was noticed is the safe default; use a later awareness time only with a recorded reason.

Copy the evidence before it ages out. Several sources delete old records on their own:

- **Audit log.** Every organization's sign-ins, failed sign-ins, member and key changes, credential changes and exports, with the actor, IP address and time. Admins read it under **Settings → Audit log**; download it as JSON Lines with `GET /api/v1/orgs/{org_id}/audit/export.jsonl`. Its hash chain shows whether rows were changed. Events older than `AUDIT_RETENTION_DAYS` (default 365) are deleted.
- **Login attempts and sessions.** The `login_attempts` table (email and IP address, kept 1 day) and the `sessions` table (IP address and user agent, until the session expires). Export what concerns the incident now:

  ```bash
  spl exec -T postgres psql -U postgres -d spanlight -c \
    "COPY (SELECT * FROM login_attempts) TO STDOUT WITH CSV HEADER" > login_attempts.csv
  ```

- **Request logs.** The api writes one JSON access line per request (method, path, status and duration with a `request_id`; no IP address or query string), and the worker one line per finished job. Compose: `spl logs --no-color --since 72h api worker > app-logs.txt`. Railway: `railway logs --service api` and `railway logs --service worker`; Railway also keeps edge logs for the web service. Railway keeps logs only for your plan's retention period, so save them first.
- **Error reports** in Sentry, when `SENTRY_DSN` is set.
- **Deploy and secret history**: the last deploys ([deploy.md](deploy.md)) and secret changes ([rotate-secrets.md](rotate-secrets.md)).

Keep the copies outside the affected system, readable only by the people handling the incident: they contain personal data too.

### 2. Contain

Stop further access before you investigate at length:

- A leaked API key or access token, a stolen session: [revoke-key.md](revoke-key.md).
- A leaked `SECRET_KEY`, `CREDENTIALS_KEYS`, database password, provider key or object storage credential: [rotate-secrets.md](rotate-secrets.md).
- A code defect that exposes data: roll back to a version without it ([rollback.md](rollback.md)) or turn the feature off, then fix it.
- A public bucket: make it private, then rotate its credentials.

Record each action and its time.

### 3. Assess the risk

Answer, and write down, for each kind of data affected:

- What happened, when it started, when it was stopped.
- Which data categories (account data, trace content, credentials) and roughly how many people and records. For trace content, list the affected organizations and projects.
- Whether the data was protected in a way that makes it unintelligible to whoever got it, for example stored secrets sealed with `CREDENTIALS_KEYS` when the key itself did not leak.
- The likely consequences for the people affected: identity theft, fraud, loss of confidentiality of what they typed into an LLM, account takeover.

Then classify: **no risk** (record only), **risk** (notify the authority), **high risk** (also notify the people affected).

### 4. Decide

Decide, and record why, for each duty in [the clocks](#the-clocks):

- Authority notification needed? If not, record the reason the breach is unlikely to result in a risk.
- Customers to notify as processor? Every organization whose trace content or members' account data was affected (the data processing agreement covers staff account data processed for the customer), whatever your own risk assessment: the customer decides on their own notifications.
- People to notify? Not needed, even at high risk, when one of the Art. 34(3) exceptions applies: the data was unintelligible to the recipient (for example strongly encrypted with a key that did not leak); measures taken since make the high risk no longer likely; or contacting each person would take disproportionate effort, in which case a public communication that informs them as effectively is made instead. Record which exception applies and why. The authority can still order a notification (Art. 34(4)).

### 5. Notify

**The supervisory authority** (Art. 33(3)), through its online breach form where it has one. Include:

1. The nature of the breach, with the categories and approximate number of people and of records concerned.
2. The name and contact details of the contact point for more information: the privacy contact named in the privacy policy.
3. The likely consequences.
4. The measures taken or proposed, including those to limit its possible adverse effects.

If more than 72 hours have passed since awareness, add the reasons for the delay.

**Customers, as processor.** Email the owners of each affected organization with the same four points, limited to their data, and offer help with their own notifications. List the owners with:

```bash
spl exec -T postgres psql -U postgres -d spanlight -c \
  "SELECT o.id AS org_id, o.name, u.email
     FROM organizations o
     JOIN memberships m ON m.org_id = o.id AND m.role = 'owner'
     JOIN users u ON u.id = m.user_id
    WHERE o.id IN ('<org id>', '<org id>')"
```

**The people affected**, at high risk (Art. 34(2)): in clear and plain language, the nature of the breach and points 2 to 4 above, plus what they can do to protect themselves (for example change their password, revoke their keys).

Keep a copy of every notification sent, with its time and recipients.

### 6. Record

Add the breach to the breach register (Art. 33(5)), also when nothing was notified. Each entry holds:

- the facts: what happened, when it started, when you became aware, when it was contained;
- the effects: data categories, approximate number of people and records, likely consequences;
- the remedial action taken;
- the decisions from step 4 and the reasons for each, including any exception relied on;
- the notifications sent, with times, and the reasons for any delay.

The authority may ask to see the register at any time, so keep it complete and dated.

### 7. Review

Within two weeks, review the incident: the root cause, why it was not caught earlier, and what changes so it does not happen again. Track each follow-up to completion and add the outcome to the register entry. If the breach came from a security report, follow the disclosure steps in the [security policy](../../SECURITY.md).

## Verify

- Every duty in [the clocks](#the-clocks) has a decision with a reason in the register entry.
- Every notification sent has a time that meets its deadline, or a recorded reason for the delay.
- The contained access is really gone: a revoked key answers 401, rotated secrets are in use ([rotate-secrets.md](rotate-secrets.md) has the checks), the fixed version is deployed.

## Related

- [Security policy](../../SECURITY.md): how vulnerabilities are reported and disclosed.
- [revoke-key.md](revoke-key.md), [rotate-secrets.md](rotate-secrets.md), [rollback.md](rollback.md): containment.
