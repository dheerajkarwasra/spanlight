---
title: The Doctor
description: Findings about how your application calls LLMs, found from your own traces. What the 15 detectors read, when they fire, how an insight is opened, muted and resolved, the health score, and the fix and check for each finding.
sidebar:
  order: 1
---

The **Doctor** reads the traces Spanlight already stores and opens an **insight** when it finds a pattern that usually costs money, time or correctness: a client that hammers a failing endpoint, a timeout that cuts off healthy calls, a conversation that grows without bound. Every insight carries the measured evidence, the layer where the problem lives, a suggested fix and a way to check that the fix worked.

The Doctor is deterministic. Fifteen detectors apply fixed rules to your spans; no language model decides whether something is a problem. An optional, clearly labelled [explanation from Claude](#explain-with-claude) can be asked for on demand, and that is the only place a model is involved.

The rules have fixed thresholds that have not been tuned against real projects, so a finding can be a false alarm for your traffic; the evidence traces are there so you can check.

The **Doctor** screen lists a project's insights. The Overview shows a [health score](#the-health-score), and a trace that is part of an insight's evidence carries a badge that links to it. Anyone who can see the project can read insights. Organization **admins and owners** can acknowledge, resolve, mute and unmute them, and ask for explanations.

## What the Doctor reads

Detectors work from the LLM and tool spans of a project. Spanlight 0.5.0 adds three fields to LLM spans so that client behaviour can be seen at all.

**Error class.** Every failed span gets one class, worked out when it is ingested from the HTTP status (`spanlight.gateway.upstream_status`, `http.status_code` or `error.status`, in that order) and, when there is no status, from the error message.

| Class | Typical cause |
| --- | --- |
| `auth` | 401 and 403, an invalid or expired key, missing permission |
| `rate_limit` | 429, "rate limit", "too many requests", quota |
| `timeout` | 408, 499 and 504, "timed out", "deadline exceeded", a client that disconnected |
| `context_length` | 413, or a 400 that talks about the context window or too many tokens |
| `content_filter` | A 400 that names a content filter, safety or a refusal |
| `provider_5xx` | 500 to 599 (529 included), "overloaded", "bad gateway" |
| `network` | Connection reset or refused, DNS, TLS |
| `client` | Any other 4xx |
| `unknown` | A failure that matches nothing above |

A span that did not fail has no class. Filter **Traces** by error class to see every trace with a failure of one class.

**Request hash.** A 32-character hash of the model and the request (the same prompt, messages, tools and parameters give the same hash). It lets the Doctor see "the same request, again". Options that do not change what is asked, such as `stream`, `user`, `metadata`, `timeout` and extra headers, are left out of the hash. The hash is computed before payload capture, so it exists even for projects that do not store prompts. The Python SDK and the gateway send it. For other sources Spanlight computes it from the span input when one is stored; only native JSON spans may send their own `request_hash` (32 lowercase hex characters).

**Finish reason.** Why generation ended, in one vocabulary for every provider: `stop`, `length`, `tool_calls`, `content_filter` or `other`. OpenAI's `stop` and Anthropic's `end_turn` and `stop_sequence` are `stop`; `max_tokens` is `length`; `tool_use` is `tool_calls`. The provider's own value stays in the span attributes. OTLP spans use `gen_ai.response.finish_reasons`.

Spans stored before these fields existed have none of them, and they are not filled in afterwards. Detectors that need a field simply skip such spans, so a new install starts finding things as new traffic arrives.

## When the Doctor runs

The worker runs every detector over every project that received a span in the last 24 hours, **every 15 minutes**. For each project it loads the last 24 hours of LLM spans and tool spans (up to 50 000 of each, newest first) and gives each detector the slice it needs, from 15 minutes to 24 hours. Baselines for the spike detectors come from the previous 7 days.

- **A run is bounded.** One pass starts no new project after 40 seconds, and one project may take at most 30 seconds. A project the pass did not reach goes first in the next one. A detector that raises an error is recorded and skipped; the others still run.
- **Nothing is invented from thin data.** Every detector has a minimum sample size and returns nothing below it. A baseline needs at least 100 calls in the same slice; without one, the spike detectors say nothing.
- **Faults and cache hits are not traffic.** Calls that the [Integration Lab](/docs/gateway/lab/) faulted on purpose are left out of the detectors that judge the health of your traffic or your provider (`error_spike`, `latency_regression`, `cost_spike`, `rate_limit_pressure`, `provider_incident`, `unpriced_spend`). Gateway cache hits never reached a provider and are left out of `retry_storm`, `cache_opportunity` and `unpriced_spend`. The detectors that judge client behaviour (the lab findings) use the faulted calls on purpose.
- **Evidence is real traces.** Each insight lists up to 20 trace ids that contributed to the finding, most recent first, and the numbers behind it. Open them to confirm the finding.

Operators can switch the schedule off with `DETECTORS_ENABLED` (see [Configuration](/docs/configuration/)) and read each run's outcome at `GET /api/v1/projects/{project_id}/detector-runs`; see the [detectors runbook](/docs/runbooks/detectors/).

## The life of an insight

An insight is identified by its **kind** and a key that names the thing it is about (for example the environment and model). The same problem is the same insight every time it is seen, so a problem that comes back does not create a second row.

| Status | Meaning |
| --- | --- |
| `open` | Found and not dealt with. |
| `acknowledged` | Someone has seen it. It still counts as open for the health score and still updates. |
| `muted` | Silenced until a date. It keeps counting occurrences but never notifies. |
| `resolved` | Gone for 24 hours, or resolved by hand. |

What happens when the detector runs:

- **A new problem** opens an insight with 1 occurrence.
- **A problem still there** adds to `occurrences`, refreshes the evidence and the last-seen time, and follows the latest severity.
- **A resolved problem that returns** reopens the same insight.
- **A problem not seen for 24 hours** after its last sighting is resolved automatically, provided its detector ran without an error.
- **A muted insight** stays muted and counts silently until the mute ends. After that it reopens if the problem is still there and resolves if it is not.

Actions, for admins and owners: **Acknowledge** (open to acknowledged), **Resolve** (any status but resolved; the insight reopens if the problem is seen again), **Mute** with a date up to 90 days ahead and a required reason (1 to 500 characters), and **Unmute**. An action that does not apply to the current status answers `409 INVALID_TRANSITION`; a bad mute date or reason answers `422 INVALID_MUTE`. Every action is written to the audit log.

Resolved insights are kept for the project's retention period and then deleted.

### Notifications

Only an insight of **critical** severity that becomes `open` sends a notification, and only once per opening. Warnings and infos appear in the Doctor and in the [weekly digest](/docs/budgets/#the-weekly-digest) but never page anyone. A re-detection of an open insight does not notify again, and a warning that later grows to critical stays quiet: only a transition into `open` notifies. A muted insight does not notify until its mute ends.

Where it goes is set per project under **Settings, Project, Insight notifications**: up to 10 of the organization's [alert channels](/docs/alerts/#channels). Admins choose the channels; a channel that is not in the organization is refused with `422 UNKNOWN_CHANNEL`. All four kinds of channel receive it:

- **Slack:** a message titled "Critical insight in {project}" with the title, the summary, the kind, the number of occurrences, when it was first seen, the suggested fix and a button that opens the insight.
- **Email:** subject `[Spanlight] Critical insight in {project}: {title}`, with the summary, the fix, the way to verify it and a link. Recipients must be verified members, as for alerts.
- **Webhook:** a signed `insight.opened` request, see [Alert webhooks](/docs/webhooks/#insightopened).
- **PagerDuty:** a `trigger` with severity `critical` and a dedup key that is stable for the insight, so a reopening does not page a second incident while the first is still open in PagerDuty. Insights never send a `resolve` to PagerDuty: an insight resolves after a quiet day, which is not a recovery signal, so close the incident in PagerDuty yourself.

The [weekly digest](/docs/budgets/#the-weekly-digest) gains an Insights section: the number of open insights and up to five of them, critical first.

## The health score

The Overview shows a **Health** tile from 0 to 100. It is 100 minus four penalties, each rounded to a whole number:

| Penalty | Formula | At most |
| --- | --- | --- |
| Open findings | 15 per critical insight and 5 per warning (open and acknowledged; infos do not count) | 40 |
| Errors | 300 times the error rate of the window (a 10 % error rate costs 30 points) | 30 |
| Latency | 15 times (p95 divided by the p95 of the previous window, minus 1), only when p95 went up | 15 |
| Cost | 15 times (spend divided by the spend of the previous window, minus 1), only when spend went up | 15 |

The window is the one you picked on the Overview (24 hours by default) and the baseline is the window of equal length right before it. An input that is unknown costs nothing: a missing p95 never lowers the score. When the window has **no LLM calls the score is "—", not 100 and not 0**. Hover the tile to see each penalty, for example "Open findings −20" and "Errors −10". `GET /api/v1/projects/{project_id}/health` returns the same numbers, with a flag when a percentile came from rollups and is approximate.

The score is a prompt to look, not a grade: two open critical insights alone take it to 70.

## Explain with Claude

Each insight page has an **Explain with Claude** button. It asks a Claude model to explain the finding to the engineer who owns the application: the likely cause, how to confirm it in the listed traces, and a concrete fix.

- **Opt-in.** Nothing is sent anywhere until an admin or owner presses the button. It needs an Anthropic provider credential under **Gateway, Credentials** and `CREDENTIALS_KEYS` on the server; without them the button explains what is missing (`409 NOT_CONFIGURED`).
- **Admins and owners only.** Members and viewers read the explanations already stored, and see the button disabled.
- **A monthly budget.** Each organization may spend `EXPLAIN_MONTHLY_BUDGET_USD` (default $1.00) per UTC month on explanations; an explanation typically costs a few cents (the worst case is reserved up front). When the budget is spent the request is refused with `402 EXPLAIN_BUDGET_EXCEEDED` and nothing is sent. The cost of an explanation in flight is reserved up front, so concurrent requests cannot overspend. The budget `0` turns explanations off. The model is `EXPLAIN_MODEL` (default `claude-sonnet-5-5`); it needs a price, otherwise the budget cannot be enforced (`409 EXPLAIN_MODEL_UNPRICED`).
- **Labelled advisory.** Every explanation is shown under "Advisory — generated by Claude from the evidence above; verify before acting." and is rendered as plain text. It is a second opinion on measured data, not a measurement.
- **What is sent.** The insight's label, title, summary, layer, metrics and window, the catalogue fix, and at most five example traces: model, status, error class, status message, finish reason, token counts and the first 1 KB of each trace's input and output. Every piece of text passes through the same secret redaction as ingestion before it leaves. A project that does not capture payloads has no prompts or completions to send. No other data, and nothing from other projects, is included.
- **It is traced.** The call goes through the gateway to your own Anthropic credential and is recorded as a normal LLM span in the project, in the environment `doctor` with the tag `doctor-explain`. It is real spend in that project.
- **Failures usually cost nothing.** A failed call answers `502 EXPLAIN_FAILED`. When the request never left, or Anthropic answered an error before generating, the reservation is removed and nothing is counted. When the provider may have billed the call (a timeout or a lost connection after the request was sent), the worst-case cost stays in the month's spend, so the budget errs high.

## The catalogue

Fifteen kinds, in three groups. **Where it fails** names the layer an insight points at: `client` is how your code calls the API, `request` is what it sends, `agent` is tool orchestration, `provider` is the model provider, `platform` is Spanlight's own setup, and `traffic` is a symptom whose cause is still open. **Certainty** is *Measured* when the insight restates numbers Spanlight observed and *Inferred* when a pattern strongly suggests a cause that you should confirm in the traces.

| Kind | Label | Layer | Certainty | Severity |
| --- | --- | --- | --- | --- |
| `error_spike` | Error spike | traffic | Measured | warning, critical at 25 % errors |
| `latency_regression` | Latency regression | traffic | Measured | warning |
| `cost_spike` | Cost spike | traffic | Measured | warning, critical at 10 times the baseline |
| `retry_storm` | Retry storm | client | Measured | warning, critical at 20 repeats |
| `retry_after_ignored` | Retry-After ignored | client | Measured | warning |
| `rate_limit_pressure` | Rate-limit pressure | provider | Measured | warning, critical at 30 % |
| `truncated_outputs` | Truncated outputs | request | Measured | warning |
| `context_growth` | Context growth | request | Inferred | warning, critical at 150 000 tokens |
| `cache_opportunity` | Prompt-cache opportunity | request | Inferred | info |
| `tool_loop` | Tool loop | agent | Measured | warning, critical at 10 repeats |
| `truncated_stream_accepted` | Truncated stream accepted | client | Measured | critical |
| `unsupported_parameter_retried` | Rejected parameter dropped | client | Measured | warning |
| `client_timeout_misconfigured` | Client timeout too short | client | Inferred | warning |
| `unpriced_spend` | Unpriced spend | platform | Measured | info |
| `provider_incident` | Provider incident | provider | Measured | warning, critical at 50 % |

In the entries below, "calls" means LLM spans, a "window" ends at the moment the detector runs, and shares and rates are shown to one decimal place in the insight text.

### Traffic

These three compare the recent past with the previous 7 days. They need a baseline of at least 100 calls in the same slice, and they ignore calls the Integration Lab faulted.

#### `error_spike`: error rate jumped

- **Reads:** LLM calls of the last 15 minutes, per environment and model, against that slice's error rate over the previous 7 days.
- **Fires when:** the slice has at least 50 calls and its error rate is at least the larger of three times the baseline and the baseline plus 5 points (so it is also at least 5 %). Warning; critical when the error rate is 25 % or more.
- **Guards:** needs a baseline; lab faults are not counted, minimum sample included.
- **Fix:** open the example traces and filter by error class. One class usually names the cause: `rate_limit` means slow down or raise limits, `auth` means rotate the key, `provider_5xx` means add a fallback target to the gateway route, `context_length` means trim the input.
- **Verify:** the error rate on Overview stays under the threshold, and the insight resolves after 24 hours without a recurrence.

#### `latency_regression`: p95 rose

- **Reads:** successful calls of the last hour with a known duration, per model, against the model's p95 over the previous 7 days.
- **Fires when:** the model has at least 100 successful calls and its p95 is at least 1.5 times the baseline and at least 500 ms above it. Warning. The p95 is exact over the observed calls (nearest rank).
- **Guards:** failed calls never count, because a fast error would hide a slow tail; needs a baseline; lab faults are not counted.
- **Fix:** compare a slow trace with a fast one. Longer inputs or outputs, a new release, or a provider slowdown are the usual causes. Cap `max_tokens`, trim context, or route latency-sensitive calls to a faster model.
- **Verify:** p95 for the model returns under 1.5 times its baseline in Overview's models table.

#### `cost_spike`: spend jumped

- **Reads:** priced calls of the last hour, per environment, against the environment's hourly spend over the previous 7 days. The baseline averages over **active hours** only, the hours that had at least one call, so quiet nights and weekends do not lower the bar for a normal busy hour or a nightly batch.
- **Fires when:** the environment has at least 20 priced calls and at least $1 of spend, and the spend is at least the larger of three times the baseline hourly spend and the baseline plus $1. Warning; critical at 10 times the baseline.
- **Guards:** unpriced calls never count (an unknown cost is not zero); needs a baseline; when the baseline is zero the ratio is shown as "—" and the finding stays a warning.
- **Fix:** check the models table for the model driving the spend and the [Users](/docs/releases-and-users/#users) page for a single user behind it. Add a [budget](/docs/budgets/) with action `block` on the gateway key to cap it.
- **Verify:** hourly spend returns under three times the baseline, and a blocking budget on the key holds.

### Client behaviour

These judge what your code does when a call fails. They use the Integration Lab's faulted calls as evidence, because a fault is how a client's reaction is seen on demand. Reproduce each with the matching scenario from [Integration Lab](/docs/gateway/lab/) and the example clients in the repository's `examples/` folder (see `examples/README.md`): a deliberately naive client that opens each finding and a corrected one that opens none.

Several of these look at calls **within one trace**, because a trace is one logical request. Send one trace per logical request: set `x-spanlight-trace-id` on each request when you call through the gateway (see the [gateway quickstart](/docs/gateway/quickstart/)), or let the SDK's trace wrap the request and its retries. A new request in the same session after a failure is correct behaviour and is never counted as a retry.

#### `retry_storm`: identical failing requests, repeated

- **Reads:** LLM calls of the last 15 minutes that have a request hash, per environment and model. Gateway cache hits are ignored.
- **Fires when:** the group has at least 20 hashed calls and contains a **storm**: 5 or more calls with the same request hash, in the same trace or the same session, starting within 60 seconds of the first one, where the first one failed. One storm is enough. Warning; critical when a storm has 20 or more calls.
- **Guards:** identical requests from unrelated traces and sessions (several users sending the same fixed prompt, a parallel fan-out) are never a storm: only one flow can retry its own request. The Integration Lab's faulted calls count, since a naive client's reaction to a fault is the point.
- **Fix:** retry with exponential backoff and jitter, at most 3 attempts, and only on retryable errors (429, 5xx, timeouts). Never retry 400, 401 or 403. The official SDKs do this with `max_retries=2` (three attempts in all).
- **Verify:** run the `provider_5xx` lab scenario on a test key; one round shows at most 3 attempts with growing gaps.

A hand-written loop that does the right thing:

```python
import random
import time

import openai

client = openai.OpenAI(max_retries=0)  # one retry policy: the loop below


def _retryable(err: openai.APIError) -> bool:
    if isinstance(err, openai.APIStatusError):
        # 408, 429 and 5xx can pass on their own. 400, 401 and 403 never will.
        return err.status_code in (408, 429) or err.status_code >= 500
    return True  # connection errors and timeouts


def create_with_backoff(attempts: int = 3, **request):
    for attempt in range(1, attempts + 1):
        try:
            return client.chat.completions.create(**request)
        except (openai.APIConnectionError, openai.APIStatusError) as err:
            if not _retryable(err) or attempt == attempts:
                raise
        time.sleep(min(30.0, 2 ** (attempt - 1)) * random.uniform(0.5, 1.5))
```

If you use the official clients without `max_retries=0`, they already back off for you; the Doctor opens this finding for code that retries by hand, in a framework, or through a proxy that retries too.

#### `retry_after_ignored`: retried before the provider said it was safe

- **Reads:** calls of the last hour.
- **Fires when:** there are at least 3 **pairs** in an environment. A pair is a `rate_limit` call that carried a `Retry-After` (the gateway records it as `retry_after_s`, from the provider's `retry-after-ms` when it sends one, else from `retry-after`), followed in the same trace or the same session by a call with the same request hash that **starts after the 429 ended** but less than `Retry-After` seconds after it. A retry that waited long enough is not a pair, and a retry that started before the 429 ended is not a retry of it. Warning.
- **Guards:** the Integration Lab's `rate_limited` calls count; each 429 pairs with at most one retry.
- **Fix:** on a 429, read `Retry-After` (or `retry-after-ms`) and wait at least that long before retrying.
- **Verify:** run the `rate_limited` lab scenario; the retry starts no earlier than `retry_after_s` after the 429.

```python
import time

import openai

MAX_WAIT_S = 30.0


def retry_delay(err: openai.RateLimitError) -> float | None:
    """Seconds the provider asked us to wait, or None when it did not say."""
    headers = err.response.headers
    try:
        if (millis := headers.get("retry-after-ms")) is not None:
            return float(millis) / 1000
        if (seconds := headers.get("retry-after")) is not None:
            return float(seconds)  # may also be an HTTP date; treat that as unknown
    except ValueError:
        pass
    return None


def wait_for_rate_limit(err: openai.RateLimitError, attempt: int) -> None:
    delay = retry_delay(err)
    if delay is None:
        delay = min(30.0, 2.0 ** attempt)  # no hint: back off
    if delay > MAX_WAIT_S:
        raise err  # the provider wants a long pause: surface it, do not hold a request open
    time.sleep(delay + 0.1)
```

#### `truncated_stream_accepted`: a cut-off stream was used as the answer

- **Reads:** calls of the last hour.
- **Fires when:** a call the Integration Lab cut short with `truncated_stream` is followed within 5 minutes, in the same trace, by a call with a different request hash and by no later call with the same hash. That is a client that carried on instead of retrying the request or failing. One such round is enough. Critical.
- **Guards:** the faulted call must have a request hash.
- **Fix:** treat a stream as complete only when its terminal event arrives (a `finish_reason` on OpenAI, `message_stop` on Anthropic); otherwise retry the request or raise.
- **Verify:** run the `truncated_stream` lab scenario; the client retries the same request or raises.

```python
class IncompleteStreamError(RuntimeError):
    """The stream ended without a terminal event, so the answer may be cut off."""


def read_chat_stream(stream) -> str:
    parts: list[str] = []
    finish_reason = None
    for chunk in stream:
        if not chunk.choices:
            continue
        choice = chunk.choices[0]
        if choice.delta.content:
            parts.append(choice.delta.content)
        if choice.finish_reason is not None:
            finish_reason = choice.finish_reason
    if finish_reason is None:
        raise IncompleteStreamError("stream ended without a finish_reason")
    return "".join(parts)
```

With Anthropic, check that a `message_stop` event arrived before you treat the text as complete.

#### `unsupported_parameter_retried`: a rejected parameter was dropped and the call resent

- **Reads:** calls of the last hour.
- **Fires when:** a call the Integration Lab rejected with `unsupported_parameter` (a 400) is followed within 60 seconds, in the same trace, by a call with a different request hash, whatever its status. One round is enough. Warning.
- **Guards:** a 400 with no follow-up is correct behaviour and opens nothing.
- **Fix:** treat a 400 as a bug: fail loudly, log the parameter and fix the call for that model instead of changing the request at run time. A request that quietly changes shape hides a real incompatibility and makes the same prompt behave differently from one model to the next.
- **Verify:** run the `unsupported_parameter` lab scenario; the client raises and sends nothing further.

#### `client_timeout_misconfigured`: the client gives up at a fixed time

- **Reads:** calls of the last 24 hours, per model. An abort is a call that failed with a timeout and has no gateway attributes (the SDK's own timeout), or that the gateway saw the client disconnect from. A `504` the gateway returned itself is not an abort.
- **Fires when:** the model has at least 10 aborts, at least 80 % of them end within 10 % of the same duration (taken from the busiest whole-second bucket), and either the model has no successful calls in the window or that duration is below the p95 of the successful ones. The successes compared are the model's calls in the same environment as the aborts. In words: a lot of calls are cut at the same moment, and healthy calls take longer than that moment. Warning.
- **Guards:** cache hits and calls the Integration Lab faulted (a cut body or stream answers fast) are not successes for the comparison, and neither are successes in another environment, which may be another client with another timeout.
- **Fix:** set the client timeout above the model's p99 (60 s is a safe start for chat) and stream long generations so the first token arrives early.
- **Verify:** run the `slow_response` lab scenario (5 s delay); the call completes instead of aborting.

```python
import httpx
import openai

client = openai.OpenAI(
    # connect fast-fail, but give the model time to answer; stream long generations
    timeout=httpx.Timeout(60.0, connect=5.0),
    max_retries=0,  # one policy: add your own backoff (see retry_storm) rather than stacking SDK retries
)

# Override for one slow call without changing the default.
slow = client.with_options(timeout=180.0)
```

### Provider and traffic shape

#### `rate_limit_pressure`: many calls get a 429

- **Reads:** calls of the last 15 minutes, per environment and provider.
- **Fires when:** the group has at least 50 calls and at least 10 % of them were rate limited. Warning; critical at 30 %.
- **Guards:** lab faults are not counted, so a simulated 429 never suggests a real limit.
- **Fix:** smooth bursts with a client-side queue or token bucket, spread load with a fallback target on the gateway route, or ask the provider for a higher limit.
- **Verify:** filter Traces by error class `rate_limit`; the share stays under 10 %.

#### `provider_incident`: a provider is failing for the whole organization

- **Reads:** the last 30 minutes of 5xx responses and calls per provider across **all projects of the organization**.
- **Fires when:** a provider has at least 20 5xx responses and they are at least 20 % of its calls, spread over at least 2 projects, or 50 or more errors in a single project. It is shown in a project only when that project had at least one such error itself. Warning; critical at 50 % or more.
- **Guards:** lab faults are not counted, so a fault-injected 5xx never opens an incident. The evidence lists only this project's traces.
- **Fix:** check the provider's status page, add a fallback target on another provider to the gateway route, and keep retries bounded.
- **Verify:** the 5xx share for the provider falls under 20 %; the insight resolves after 24 hours without a recurrence.

### The request itself

#### `truncated_outputs`: answers stop at the token limit

- **Reads:** calls of the last hour that have a finish reason, per model. A call without one is not counted at all; unknown is not `stop`.
- **Fires when:** the model has at least 50 such calls and at least 10 % of them ended with `length`. Warning.
- **Fix:** raise `max_tokens` for this call site, ask for shorter output in the prompt, or detect `finish_reason == "length"` and continue the generation.
- **Verify:** the share of `length` finish reasons for the model stays under 10 %.

#### `context_growth`: the conversation history grows without bound

- **Reads:** calls of the last 24 hours that belong to a session and have input tokens.
- **Fires when:** a session has at least 8 calls, input tokens rise on at least 80 % of consecutive calls, and the last call is at least three times the first and at least 20 000 tokens. An environment fires on 3 such sessions, or on one whose last call reached 100 000 tokens. Warning; critical when a last call reached 150 000.
- **Fix:** keep a sliding window of recent turns, summarise older turns, or move reference material to retrieval instead of the message history.
- **Verify:** open a long session in Sessions; input tokens per call level off instead of climbing.

#### `cache_opportunity`: long repeated prompts are not cached

- **Reads:** calls of the last 24 hours on a model that supports prompt caching (`claude-`, `gpt-4o`, `gpt-4.1`, `gpt-5`, `o1`, `o3`, `o4`), excluding gateway cache hits.
- **Fires when:** a request hash with at least 2 000 input tokens and no cached tokens was sent at least 10 times across at least 2 traces. Info. This is a capability list, not a price check.
- **Fix:** put the stable prefix (system prompt, tools, documents) first. On Anthropic mark it with `cache_control: {"type": "ephemeral"}`; OpenAI caches identical prefixes of 1,024 tokens or more automatically. For exact repeats, enable the [gateway cache](/docs/gateway/cache/) on the key.
- **Verify:** the next calls show cached tokens in the trace's usage card.

### Agents and setup

#### `tool_loop`: the agent repeats a tool call

- **Reads:** tool spans of the last hour.
- **Fires when:** within one trace, 4 or more consecutive tool spans have the same name and the same stored input. A span whose input is not stored never counts, so a project that does not capture payloads cannot see this finding. Warning; critical at 10 in a row.
- **Also matches:** deliberate polling of the same tool with the same arguments. Mute the insight if that is what the trace shows.
- **Fix:** pass each tool result back to the model, cap tool iterations per turn, and stop when a call repeats with the same arguments.
- **Verify:** no trace shows 4 or more identical consecutive calls to the tool.

#### `unpriced_spend`: usage without a price

- **Reads:** calls of the last 24 hours that report both token counts and have no cost, which means the price table has no match for the model.
- **Fires when:** a provider and model have at least 20 such calls. Info.
- **Guards:** lab faults and cache hits are not counted.
- **Fix:** add an organization price override for the model, or check that the model name matches the provider's (snapshot suffixes like `-20250101` match automatically).
- **Verify:** new calls to the model show a cost on the trace detail.

## Trying it on purpose

The five client-behaviour findings can all be reproduced. The example clients in the repository's `examples/` folder run a scenario against a gateway key that has a fault profile:

| Lab scenario | Naive client opens |
| --- | --- |
| `auth_expired`, `scope_denied`, `malformed_json`, `provider_5xx` | `retry_storm` |
| `rate_limited` | `retry_after_ignored` and `retry_storm` |
| `truncated_stream` | `truncated_stream_accepted` |
| `unsupported_parameter` | `unsupported_parameter_retried` |
| `slow_response`, `timeout` | `client_timeout_misconfigured` (and `retry_storm`, because the naive client retries its own timeouts at once) |

The corrected client runs the same scenarios and is written to open no finding; keep `hold_ms` under 60 000, or its own 60 s timeout shows up as `client_timeout_misconfigured`. Findings appear the next time the detectors run, within about 15 minutes, if the run is long enough to pass each detector's minimum sample: use 10 or more rounds (`--rounds 12` for the timeout scenarios), because `retry_storm` needs 20 hashed calls and `client_timeout_misconfigured` needs 10 aborts.

## Settings

| Variable | Default | Effect |
| --- | --- | --- |
| `DETECTORS_ENABLED` | `true` | Schedule the detector job. Off, no new insights appear. |
| `EXPLAIN_MODEL` | `claude-sonnet-5-5` | The model behind "Explain with Claude". It needs a price. |
| `EXPLAIN_MONTHLY_BUDGET_USD` | `1.00` | Most an organization may spend on explanations per UTC month. `0` turns them off. |
| `USER_STATS_ENABLED` | `true` | Schedule the refresh behind the [Users](/docs/releases-and-users/#users) page. |

Full descriptions are in [Configuration](/docs/configuration/).

## Troubleshooting

| You see | Check |
| --- | --- |
| No insights at all | The Doctor needs traffic in the last 24 hours and checks every 15 minutes. Then check `DETECTORS_ENABLED` and that the worker is running. A new install has no baseline: the traffic detectors stay quiet until a slice has 100 calls from the previous 7 days. |
| A finding you expected is missing | Each detector has a minimum sample size (see its entry). The client detectors need `request_hash`, so spans must come from the current SDK or the gateway; `tool_loop` needs stored inputs. |
| An insight does not go away | It resolves after 24 hours without a sighting. Resolve it by hand if you have fixed the cause; it reopens if the problem is seen again. |
| A critical insight did not notify | The project needs insight channels under Settings, Project. A muted insight does not notify until its mute ends, and only the first opening notifies. |
| "Explain with Claude" is disabled | You are not an admin or owner, no Anthropic credential is set, or the monthly budget is spent. The message says which. |
| Detector runs show errors | See the [detectors runbook](/docs/runbooks/detectors/). |
