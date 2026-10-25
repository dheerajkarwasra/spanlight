---
title: Detector catalogue
description: The Doctor's sixteen detectors, with the layer, certainty and severity of each, what triggers it, and the fix and check for every finding.
---

The [Doctor](/docs/doctor/) runs these detectors over your traces. This page lists each one with what it reads, when it fires, and the fix and check it suggests.

Sixteen kinds, in five groups. **Where it fails** names the layer an insight points at: `client` is how your code calls the API, `request` is what it sends, `agent` is tool orchestration, `provider` is the model provider, `platform` is Spanlight's own setup, and `traffic` is a symptom whose cause is still open. **Certainty** is *Measured* when the insight restates numbers Spanlight observed and *Inferred* when a pattern strongly suggests a cause that you should confirm in the traces.

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
| `model_retiring` | Model retiring | provider | Measured | warning, critical 14 days before the date |

In the entries below, "calls" means LLM spans, a "window" ends at the moment the detector runs, and shares and rates are shown to one decimal place in the insight text.

Each insight page asks **Was this right?**. The votes give every detector a precision on your own traffic, shown on the Doctor's **Precision** tab with a Wilson 95 % interval. A detector meets the target at a precision of at least 0.8 with the lower end of the interval at least 0.6, over at least 20 votes. See [The Doctor](/docs/doctor/#was-this-right).

## Traffic

These three compare the recent past with the previous 7 days. They need a baseline of at least 100 calls in the same slice, and they ignore calls the Integration Lab faulted.

### `error_spike`: error rate jumped

- **Reads:** LLM calls of the last 15 minutes, per environment and model, against that slice's error rate over the previous 7 days.
- **Fires when:** the slice has at least 50 calls and its error rate is at least the larger of three times the baseline and the baseline plus 5 points (so it is also at least 5 %). Warning; critical when the error rate is 25 % or more.
- **Guards:** needs a baseline; lab faults are not counted, minimum sample included. Calls refused by a gateway key's own rate limit are not counted either: they never reached a model.
- **Fix:** open the example traces and filter by error class. One class usually names the cause: `rate_limit` means slow down or raise limits, `auth` means rotate the key, `provider_5xx` means add a fallback target to the gateway route, `context_length` means trim the input, `gateway` means Spanlight's gateway refused the call (a budget, for example).
- **Verify:** the error rate on Overview stays under the threshold, and the insight resolves after 24 hours without a recurrence.

### `latency_regression`: p95 rose

- **Reads:** successful calls of the last hour with a known duration, per model, against the model's p95 over the previous 7 days.
- **Fires when:** the model has at least 100 successful calls and its p95 is at least 1.5 times the baseline and at least 500 ms above it. Warning. The p95 is exact over the observed calls (nearest rank).
- **Guards:** failed calls never count, because a fast error would hide a slow tail; needs a baseline; lab faults are not counted.
- **Fix:** compare a slow trace with a fast one. Longer inputs or outputs, a new release, or a provider slowdown are the usual causes. Cap `max_tokens`, trim context, or route latency-sensitive calls to a faster model.
- **Verify:** p95 for the model returns under 1.5 times its baseline in Overview's models table.

### `cost_spike`: spend jumped

- **Reads:** priced calls of the last hour, per environment, against the environment's hourly spend over the previous 7 days. The baseline averages over **active hours** only, the hours that had at least one call, so quiet nights and weekends do not lower the bar for a normal busy hour or a nightly batch.
- **Fires when:** the environment has at least 20 priced calls and at least $1 of spend, and the spend is at least the larger of three times the baseline hourly spend and the baseline plus $1. Warning; critical at 10 times the baseline.
- **Guards:** unpriced calls never count (an unknown cost is not zero); needs a baseline; when the baseline is zero the ratio is shown as "—" and the finding stays a warning.
- **Fix:** check the models table for the model driving the spend and the [Users](/docs/releases-and-users/#users) page for a single user behind it. Add a [budget](/docs/budgets/) with action `block` on the gateway key to cap it.
- **Verify:** hourly spend returns under three times the baseline, and a blocking budget on the key holds.

## Client behaviour

These judge what your code does when a call fails. They use the Integration Lab's faulted calls as evidence, because a fault is how a client's reaction is seen on demand. Reproduce each with the matching scenario from [Integration Lab](/docs/gateway/lab/) and the example clients in the repository's `examples/` folder (see `examples/README.md`): a deliberately naive client that opens each finding and a corrected one that opens none.

Several of these look at calls **within one trace**, because a trace is one logical request. Send one trace per logical request: set `x-spanlight-trace-id` on each request when you call through the gateway (see the [gateway quickstart](/docs/gateway/quickstart/)), or let the SDK's trace wrap the request and its retries. A new request in the same session after a failure is correct behaviour and is never counted as a retry.

### `retry_storm`: identical failing requests, repeated

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

### `retry_after_ignored`: retried before the provider said it was safe

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

### `truncated_stream_accepted`: a cut-off stream was used as the answer

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

### `unsupported_parameter_retried`: a rejected parameter was dropped and the call resent

- **Reads:** calls of the last hour.
- **Fires when:** a call the Integration Lab rejected with `unsupported_parameter` (a 400) is followed within 60 seconds, in the same trace, by a call with a different request hash, whatever its status. One round is enough. Warning.
- **Guards:** a 400 with no follow-up is correct behaviour and opens nothing.
- **Fix:** treat a 400 as a bug: fail loudly, log the parameter and fix the call for that model instead of changing the request at run time. A request that quietly changes shape hides a real incompatibility and makes the same prompt behave differently from one model to the next.
- **Verify:** run the `unsupported_parameter` lab scenario; the client raises and sends nothing further.

### `client_timeout_misconfigured`: the client gives up at a fixed time

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

## Provider and traffic shape

### `rate_limit_pressure`: many calls get a 429

- **Reads:** calls of the last 15 minutes, per environment and provider.
- **Fires when:** the group has at least 50 calls and at least 10 % of them were rate limited. Warning; critical at 30 %.
- **Guards:** lab faults are not counted, so a simulated 429 never suggests a real limit. Calls refused by a gateway key's own rate limit (rate-limit kind `gateway`) are not counted either, so Spanlight's limiter never reads as provider pressure.
- **Fix:** read the rate-limit kind on [Provider health](/docs/guides/provider-health/#the-rate-limit-kinds) first: a `spend_cap` needs money on the account, not a slower client. For `tokens` and `requests`, smooth bursts with a client-side queue or token bucket, spread load with a fallback target on the gateway route, or ask the provider for a higher limit.
- **Verify:** filter Traces by error class `rate_limit`; the share stays under 10 %.

### `provider_incident`: a provider is failing for the whole organization

- **Reads:** the last 30 minutes of 5xx responses and calls per provider across **all projects of the organization**. Only error class `provider_5xx` counts: a 500 or 503 that Spanlight's own gateway made is class `gateway`.
- **Fires when:** a provider has at least 20 5xx responses and they are at least 20 % of its calls, spread over at least 2 projects, or 50 or more errors in a single project. It is shown in a project only when that project had at least one such error itself. Warning; critical at 50 % or more.
- **Guards:** lab faults are not counted, so a fault-injected 5xx never opens an incident. The evidence lists only this project's traces.
- **Status page:** the summary names the provider's status page incident that overlaps the window and touched its API (with 15 minutes of grace after it was resolved), or says there is none. The citation never changes when the insight opens, its severity or when it resolves. The provider is matched by its exact name, such as `openai`; see [Status page incidents](/docs/guides/provider-health/#status-page-incidents).
- **Fix:** check the provider's status page, add a fallback target on another provider to the gateway route, and keep retries bounded.
- **Verify:** the 5xx share for the provider falls under 20 %; the insight resolves after 24 hours without a recurrence.

### `model_retiring`: calls go to a model that is about to retire

- **Reads:** calls of the last 24 hours, matched against a table of the retirement dates Anthropic and OpenAI have announced, bundled with each Spanlight release.
- **Fires when:** at least one call went to a model that retires within 60 days. Warning; critical from 14 days before the date, and after it, when calls to the model fail.
- **Guards:** lab faults and cache hits are not counted. Only the provider's own API matches: a model served through Amazon Bedrock or Google Cloud follows that platform's schedule. A model name ending in `-latest` moves to newer snapshots, so it never matches a dated snapshot's date. Anthropic's "not sooner than" dates for active models are not retirement dates and are never used.
- **Fix:** move the calls to the replacement the provider recommends (named in the insight, with the routes and trace names that send the most calls), and run an [experiment](/docs/experiments/) on a dataset first to compare quality and cost.
- **Verify:** no calls to the model in the next 24 hours.

## The request itself

### `truncated_outputs`: answers stop at the token limit

- **Reads:** calls of the last hour that have a finish reason, per model. A call without one is not counted at all; unknown is not `stop`.
- **Fires when:** the model has at least 50 such calls and at least 10 % of them ended with `length`. Warning.
- **Fix:** raise `max_tokens` for this call site, ask for shorter output in the prompt, or detect `finish_reason == "length"` and continue the generation.
- **Verify:** the share of `length` finish reasons for the model stays under 10 %.

### `context_growth`: the conversation history grows without bound

- **Reads:** calls of the last 24 hours that belong to a session and have input tokens.
- **Fires when:** a session has at least 8 calls, input tokens rise on at least 80 % of consecutive calls, and the last call is at least three times the first and at least 20 000 tokens. An environment fires on 3 such sessions, or on one whose last call reached 100 000 tokens. Warning; critical when a last call reached 150 000.
- **Fix:** keep a sliding window of recent turns, summarise older turns, or move reference material to retrieval instead of the message history.
- **Verify:** open a long session in Sessions; input tokens per call level off instead of climbing.

### `cache_opportunity`: long repeated prompts are not cached

- **Reads:** calls of the last 24 hours on a model that supports prompt caching (`claude-`, `gpt-4o`, `gpt-4.1`, `gpt-5`, `o1`, `o3`, `o4`), excluding gateway cache hits.
- **Fires when:** a request hash with at least 2 000 input tokens and no cached tokens was sent at least 10 times across at least 2 traces. Info. This is a capability list, not a price check.
- **Fix:** put the stable prefix (system prompt, tools, documents) first. On Anthropic mark it with `cache_control: {"type": "ephemeral"}`; OpenAI caches identical prefixes of 1,024 tokens or more automatically. For exact repeats, enable the [gateway cache](/docs/gateway/cache/) on the key.
- **Verify:** the next calls show cached tokens in the trace's usage card.

## Agents and setup

### `tool_loop`: the agent repeats a tool call

- **Reads:** tool spans of the last hour.
- **Fires when:** within one trace, 4 or more consecutive tool spans have the same name and the same stored input. A span whose input is not stored never counts, so a project that does not capture payloads cannot see this finding. Warning; critical at 10 in a row.
- **Also matches:** deliberate polling of the same tool with the same arguments. Mute the insight if that is what the trace shows.
- **Fix:** pass each tool result back to the model, cap tool iterations per turn, and stop when a call repeats with the same arguments.
- **Verify:** no trace shows 4 or more identical consecutive calls to the tool.

### `unpriced_spend`: usage without a price

- **Reads:** calls of the last 24 hours that report both token counts and have no cost, which means the price table has no match for the model.
- **Fires when:** a provider and model have at least 20 such calls. Info.
- **Guards:** lab faults and cache hits are not counted.
- **Fix:** add an organization price override for the model, or check that the model name matches the provider's (snapshot suffixes like `-20250101` match automatically).
- **Verify:** new calls to the model show a cost on the trace detail.

## Related pages

- [The Doctor](/docs/doctor/): Findings about how your application calls LLMs.
- [Alert rules and channels](/docs/alerts/): Get told when error rate, latency, volume or cost crosses a line.
- [Alert webhooks](/docs/webhooks/): Signed requests for alerts, budgets and critical insights.
- [Run the Doctor](/docs/runbooks/detectors/): Detector runs, stuck projects and the explanation budget.
