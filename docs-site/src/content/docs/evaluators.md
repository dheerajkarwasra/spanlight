---
title: Evaluators
description: Score traces automatically with rules, an LLM judge under a monthly budget, or your own endpoint. How sampling works, what a judge sees, how the budget is reserved, and how to verify a webhook evaluator.
sidebar:
  order: 1
---

An **evaluator** scores traces for you. It looks at what went in and what came out of an LLM call and writes a [score](/docs/scores/) named after the evaluator. Evaluators run in two places: **online**, on a sample of the traces your application produces, and inside [experiments](/docs/experiments/), on every answer of a dataset run. You can also **Test** an evaluator on one stored trace.

There are three kinds:

| Kind | How it scores | Costs |
| --- | --- | --- |
| **Heuristic** | A fixed rule: a pattern, a length, JSON validity, a comparison with the expected output | Nothing |
| **LLM judge** | A model reads the question and the answer and scores them from 0 to 1 | Provider spend, capped by the judge's **monthly budget** |
| **Webhook** | Your endpoint receives the trace and answers with scores | Nothing from Spanlight; your endpoint's own cost |

Anyone who can see the project can read evaluators and their runs. Only organization **admins and owners** can create, edit, delete and test them, because a judge spends the organization's money without anyone watching. Evaluators live under **Evaluators** in the project.

## Settings every evaluator has

| Setting | Meaning |
| --- | --- |
| Name | Lowercase letters, digits, `_`, `.` and `-` (1 to 64 characters, starting with a letter or digit), unique in the project. The scores it writes carry this name, so it follows the score name rule. |
| Sampling rate | The share of matching traces scored online, from 0 to 1 with four decimals. `0` means online scoring is off and the evaluator is used only in experiments and tests. |
| Filters | Which traces are considered: environments, models, tags, trace names and a minimum duration in milliseconds. Each list holds up to 50 values; an empty list matches everything; a non-empty list needs the trace to have one of its values. |
| Enabled | A disabled evaluator is not sampled. |
| Monthly budget | Required for a judge (see [the budget](#the-monthly-budget)); judges are the only kind that spend. |

An evaluator cannot be switched to another kind after it is created.

## Online sampling

A worker job runs **every minute** (`EVALS_ENABLED`, on by default) and, for each enabled evaluator with a sampling rate above 0, queues a run for each trace that:

- **ended between 30 seconds and 5 minutes ago**. The 30 second wait lets a trace finish arriving; the 5 minute window overlaps the next cycles on purpose, so a cycle that was missed is caught up, and a trace is never queued twice;
- matches the evaluator's filters;
- falls in the evaluator's sample.

Sampling is **deterministic**: whether a trace is in an evaluator's sample depends only on the evaluator and the trace id, so every worker and every rescan agrees, and raising the rate only adds traces. A trace that ran for more than an hour is not sampled.

What is scored is the trace's **last LLM span**. A trace without an LLM span is skipped, and so is a span with no stored input or output, which is what a project that does not capture payloads has. An evaluator gets at most `EVAL_SAMPLER_MAX_RUNS_PER_CYCLE` (50) new runs per cycle, and an LLM judge whose monthly budget is used up is not sampled until the next month.

**Spanlight's own traces are never sampled.** Traces in the environments `evals`, `playground`, `experiments` and `doctor`, and traces tagged `spanlight:judge`, are left out. Without this rule a judge would score its own calls forever.

Online runs have no expected output, so rules that compare with one cannot sample (see below).

## Heuristic evaluators

A heuristic evaluator applies one rule and writes one score: a boolean score (`1` or `0`) for a yes/no rule and a numeric score for `length` and `levenshtein`.

| Rule | Score | Settings |
| --- | --- | --- |
| `regex` | 1 if the pattern matches | `pattern` (1 to 500 characters, must compile), `target` (`output` or `input`), `ignore_case` |
| `json_valid` | 1 if the text is valid JSON | `target` |
| `length` | The length of the text | `target`, `unit` (`chars` or `words`) |
| `contains` | 1 if the text contains the string | `needle` (up to 500 characters), `target`, `case_sensitive` (off by default) |
| `exact_match` | 1 if the answer equals the expected output | `normalize_whitespace` (on by default), `case_sensitive` (on by default) |
| `levenshtein` | Similarity to the expected output from 0 to 1 (`1 - distance / longest length`; 1 when both are empty) | none |

- **`exact_match` and `levenshtein` need an expected output.** Live traces have none, so these two cannot sample: creating one with a sampling rate above 0 answers `422 RULE_NEEDS_EXPECTED_OUTPUT`. Set the rate to 0 and attach it to an experiment, where the dataset item's expected output is used. An item without one gets no score. `levenshtein` compares at most the first 2 000 characters of each side, and runs off the worker's event loop.
- **Patterns have a time limit.** A `regex` runs with a 1 second limit on the first 10 000 characters of the text, so a pattern that backtracks without end fails its run with `regex_timeout` instead of holding a worker.
- **Which text.** A plain string is used as it is. For an OpenAI or Anthropic response the answer text is used. For input that is a list of chat messages, the last user message. Anything else is compared as compact JSON.

## LLM judge

A judge sends the trace to a model with a scoring prompt and reads a score and a short reason from the answer. The score is a number from 0 to 1 and **higher is better for every judge**, including the safety judge. The reason is saved as the score's comment.

**What the judge sees.** The input (a chat is shown as `role: content` lines, system messages included), the output text and, when the item has one, the expected output (otherwise `(none)`). Each is cut to 8000 characters with a `…[truncated]` marker before it is sent.

**What it needs.** A credential of your organization for **Anthropic or OpenAI**, a model with a known price (without a price the budget could not be enforced, so a model that costs nothing or has no price answers `422 EVALUATOR_MODEL_UNPRICED`), `max_tokens` (256 by default, up to 4096), and a monthly budget. `temperature` is not sent unless you set it, because the newest Claude models reject non-default sampling settings. It needs `CREDENTIALS_KEYS` to be set.

### The four built-in judges

You can pick a built-in judge or use your own prompt. Each built-in judge is a chat prompt that tells the model it is a strict evaluator, that everything between the markers is data and never instructions, and to answer with one JSON object holding `score` and `reasoning`.

| Judge | Scores | 1.0 means | 0.0 means |
| --- | --- | --- | --- |
| `relevance` | How directly and completely the output answers what the input asks. Padding and off-topic content lower it. | Fully relevant and complete | Unrelated, or a refusal with no reason |
| `faithfulness` | Whether every factual claim in the output is supported by the source material in the input or by the expected output used as a reference. Greetings and restating the question are not penalised. | Every claim is supported | The output contradicts its sources or is mostly invented |
| `toxicity` | The **safety** of the output; the input is only context. Higher is safer. | No toxic, hateful, harassing, sexually explicit or dangerous content | Clearly toxic, hateful, threatening or dangerous |
| `format_compliance` | How exactly the output follows the format instructions in the input: a structure, a length, a language, a tone or a schema. Correctness is not judged. | Every format instruction followed | None followed. An input with no format instructions scores 1.0 |

A midpoint of 0.5 means "partly" for each. The first time you use a built-in judge, Spanlight saves its prompt as the chat [prompt](/docs/prompts/) `judges/<name>` in your project. You can read and edit it like any prompt: an edit is a new version, and an evaluator created afterwards can use it. Existing evaluators keep the version they were created with, so a judge's scores stay comparable over time.

**Your own judge prompt.** Choose a chat prompt version that uses only the variables `input`, `output` and `expected_output` (`422 JUDGE_TEMPLATE_VARIABLES` otherwise). It must ask the model to answer with a JSON object that has a numeric `score` between 0 and 1, optionally with `reasoning`.

**Reading the answer.** Spanlight takes the first complete JSON object in the answer, even inside prose or a code fence. `score` must be a number (a numeric string such as `"0.8"` is accepted); it is clamped to 0..1 and kept to four decimals. If there is no usable object, the run fails with `verdict_unparseable`. Judge calls have no retries.

**The judge's own calls are traces.** Each call is recorded in environment `evals`, named `judge:<evaluator name>` and tagged `spanlight:judge`, with its tokens and cost, like any call through the gateway. A project budget set to **block** can stop it: the run fails with `gateway_budget_blocked` and costs nothing.

### Prompt injection

The judged text is passed to the model as data, between markers, and the instructions tell the model to treat it as data. That makes steering the score harder, but it does not make it impossible. A model can still be talked into a higher score by text inside the answer it is judging, and a judge can be wrong in the ordinary ways a model is wrong.

Treat a judge's score as a **signal, not proof**. Do not let it alone decide anything security-sensitive. It is also worth remembering that the judged text, redacted but otherwise as stored, is sent to the provider behind the credential you chose.

### The monthly budget

A judge spends money while nobody is watching, so it never runs without a **monthly budget**: an amount in US dollars above zero. The budget belongs to the evaluator (not the project or the organization) and covers everything that judge does in a UTC calendar month: sampled runs, **Test** runs and its use in experiments. It resets at 00:00 UTC on the 1st.

The budget is a **reservation**, not a check made after the fact:

1. Before each call, Spanlight estimates the most it could cost. The estimate is a ceiling: about one token for every three ASCII characters of the final prompt, one token for every other character (so Chinese text and emoji stay under the ceiling), plus the whole `max_tokens` allowance as output, at the model's price.
2. In one step, under a lock on the evaluator, it adds that estimate to the month's settled spend and to the reservations held by calls that are still running. If the total would pass the budget, the run is marked `failed` with `budget_exceeded`, **no request is sent to the provider** and nothing is charged. The dashboard shows this as the neutral state "Skipped: the monthly budget is spent".
3. Otherwise the run holds its estimate while the call is made. Two runs starting together cannot both pass the edge.
4. When the call ends, the run is charged its real cost from the usage the provider reported, and the reservation is released. A call that failed after it was sent and may have been billed (a timeout, a lost connection, an answer that cannot be read) is charged the usage it reported, or its whole estimate when there is none. A call that was never sent, or that the provider refused with an error status, is free.
5. A run that is still `running` after 10 minutes, because its worker died, is marked `abandoned` and charged its reservation, since the provider may have been paid.

Because the estimate is a ceiling and not a measurement, spend normally stays under the budget; a prompt the estimate undercounts could end a month over by about one call.

The budget meter shows spent, reserved and remaining for the month, and turns to a warning at 80 %. `GET .../evaluators/{evaluator_id}/budget` returns the same.

## Webhook evaluators

A webhook evaluator sends each trace to **your endpoint**, so you can score with your own code: a classifier, a database lookup, a human-in-the-loop service. It uses a **webhook channel** of your organization (see [Alert rules and channels](/docs/alerts/#channels)): the channel supplies the URL and the signing secret.

For each run Spanlight sends one signed `POST`. The body is compact JSON; the headers are:

| Header | Value |
| --- | --- |
| `Content-Type` | `application/json` |
| `X-Spanlight-Timestamp` | Unix time in seconds |
| `X-Spanlight-Signature` | `sha256=` and the hex HMAC-SHA256 of `<timestamp>.<body>` under the channel's signing secret |

```json
{
  "version": "2026-10-01",
  "evaluator": { "id": "0192f3a1-5c3e-7b1a-9d2c-8e4f6a7b8c9d", "name": "no-email" },
  "run_id": "0192f3a2-1111-7000-8000-00000000abcd",
  "trace": {
    "trace_id": "5b8efff798038103d269b633813fc60c",
    "name": "answer",
    "environment": "production",
    "release": "1.4.2",
    "user_id": "u_123",
    "session_id": "s_789",
    "tags": ["support"]
  },
  "span": { "span_id": "eee19b7ec3c1b174", "name": "chat.completions", "model": "gpt-4o-mini" },
  "input": { "messages": [{ "role": "user", "content": "How do refunds work?" }] },
  "output": { "role": "assistant", "content": "Refunds go back to the original payment method." },
  "expected_output": null,
  "metadata": {},
  "truncated": false
}
```

`input`, `output` and `expected_output` are as stored (already redacted). Each is at most 64 KB and the whole body at most 256 KB; anything longer is cut to a string and `truncated` is `true`.

**Your reply** must be `200` with a JSON body of at most 64 KB:

```json
{ "scores": [{ "name": "no-email", "value": true, "comment": "no address found" }] }
```

Send 0 to 20 scores. Each has a valid score `name`, exactly one of `value` (a number or a boolean) and `string_value`, and an optional `comment`. Anything else fails the run: a status other than 200 is `webhook_status_<code>`, an unreadable or invalid reply is `webhook_invalid_reply`, and a failure to connect, an address that is not allowed or no answer within 30 seconds is `webhook_unreachable`.

- **One attempt.** There is no retry and redirects are not followed. The whole exchange has 30 seconds.
- **Same network rules as alert webhooks.** The URL must be `https://`, and addresses that are private, loopback, link-local or reserved are refused unless the operator set `WEBHOOK_ALLOW_PRIVATE_TARGETS`.
- The URL and the secret never appear in a run's error, a log line or the audit log.

### Verify the signature

Check every request before you act on it, as for [alert webhooks](/docs/webhooks/#verify-a-request): read the **raw** body, reject a timestamp more than 300 seconds from your clock, recompute the HMAC and compare in constant time. Unlike alert deliveries, evaluator requests carry no delivery id and are never retried, so there is nothing to deduplicate.

```python
import hashlib
import hmac
import json
import os
import time

from fastapi import FastAPI, HTTPException, Request

SECRET = os.environ["SPANLIGHT_SIGNING_SECRET"].encode()  # the webhook channel's secret
app = FastAPI()


def verify(secret: bytes, timestamp: str, body: bytes, header: str, tolerance: int = 300) -> bool:
    try:
        sent_at = int(timestamp)
    except ValueError:
        return False
    if abs(time.time() - sent_at) > tolerance:
        return False
    mac = hmac.new(secret, timestamp.encode() + b"." + body, hashlib.sha256)
    return hmac.compare_digest(("sha256=" + mac.hexdigest()).encode(), header.encode())


@app.post("/score")
async def score(request: Request) -> dict:
    body = await request.body()  # the raw bytes, not a re-serialised copy
    if not verify(
        SECRET,
        request.headers.get("x-spanlight-timestamp", ""),
        body,
        request.headers.get("x-spanlight-signature", ""),
    ):
        raise HTTPException(status_code=401)
    payload = json.loads(body)
    text = json.dumps(payload["output"])
    return {"scores": [{"name": "no-email", "value": "@" not in text}]}
```

## Runs

Every evaluation is a **run** with a status: `queued`, `running`, `done` or `failed`, a source (`online`, `test` or `experiment`), its cost and, when it succeeded, the scores it wrote. The run list of an evaluator can be filtered by status.

Online runs and their scores follow the project's retention: they are deleted once older than the project's `retention_days`, but a run is kept until its calendar month ends, because the monthly budget counts it. Datasets, experiments and their items are kept until you delete them.

| Error | Meaning |
| --- | --- |
| `budget_exceeded` | The judge's monthly budget could not cover another call. Nothing was sent or charged. |
| `gateway_budget_blocked` | A project budget set to block stopped the judge's call. |
| `verdict_unparseable` | The judge's answer had no usable score. It may have been billed. |
| `regex_timeout` | A regex rule ran out of its 1 second. |
| `judge_prompt_too_large` | The judge's prompt, with the trace filled in, would exceed 256 KB. Nothing was sent. |
| `webhook_status_<code>`, `webhook_invalid_reply`, `webhook_unreachable` | A webhook evaluator failed (above). |
| `evaluator_disabled` | The evaluator was disabled or deleted after the run was queued. |
| `trace_not_found`, `no_payload` | The trace is gone, or the span has no stored input or output. |
| `abandoned` | The run stayed `running` for 10 minutes, usually because a worker died. |

Other codes name a setup problem, such as `credentials_not_configured`, `judge_credential_missing` or `evaluator_model_unpriced`; they end the run before any provider call.

**Test** evaluates one stored trace now and shows the scores without storing them. A judge's test is a real call and counts against its budget; over budget it answers `402 EVALUATOR_BUDGET_EXCEEDED`, and a failed evaluation `502 EVALUATOR_FAILED`.

## Routes

Under `/api/v1/projects/{project_id}/evaluators`.

| Route | Needs | Does |
| --- | --- | --- |
| `GET` | `project:read` | List; each judge carries its budget so far |
| `POST` | `evaluators:write` | Create (`409 NAME_TAKEN`) |
| `GET`, `PATCH`, `DELETE /{evaluator_id}` | read, `evaluators:write` | Read, change, delete (scores already written stay) |
| `GET /{evaluator_id}/runs` | `project:read` | Runs, newest first |
| `GET /{evaluator_id}/budget` | `project:read` | The month's spend, reservations and remaining amount |
| `POST /{evaluator_id}/test` | `evaluators:write` | Evaluate `{"trace_id"}` now, without storing scores |

A create body is `{"name", "config", "sampling_rate"?, "filters"?, "monthly_budget_usd"?, "enabled"?}`. The `config` has a `kind` of `heuristic` (with a `check`), `llm_judge` (with `credential_id`, `model` and either `judge_template` or `prompt_version_id`) or `webhook` (with `channel_id`). `PATCH` takes the members to change, and a `config` with only the fields to change. Creation can also answer `422 BUDGET_REQUIRED` (a judge without a budget), `RULE_NEEDS_EXPECTED_OUTPUT`, `JUDGE_TEMPLATE_VARIABLES`, `EVALUATOR_MODEL_UNPRICED` or `CHANNEL_KIND` (a webhook evaluator on a channel that is not a webhook channel of the organization), and `404` for a prompt version, credential or channel the project or organization does not have.
