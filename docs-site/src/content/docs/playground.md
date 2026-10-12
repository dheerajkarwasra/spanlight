---
title: Playground
description: Run a prompt or typed messages against up to three models side by side, with real provider calls recorded as traces, and learn what it costs and how it is limited.
sidebar:
  order: 1
---

The **playground** lets you try a prompt before you ship it. Pick a prompt version or write messages, choose a provider credential and a model, and run. You can put up to **three configurations** next to each other, for example the same prompt on two models, or two prompt versions on one model, and compare the answers, the tokens, the cost and the time they took.

**Every run is a real provider call.** It spends money on the credential you chose, and it is recorded as a trace like any other call. Nothing is simulated. Members, admins and owners can run the playground; viewers can read the traces it leaves.

## Set up a run

Each configuration has:

| Part | Meaning |
| --- | --- |
| Credential | A [provider credential](/docs/gateway/credentials/) of your organization (OpenAI, Anthropic or an OpenAI-compatible server). The key is used on the server and never shown. |
| Model | The model name sent to the provider. |
| Parameters | `temperature` (0 to 2), `max_tokens` (1 to 4096) and `top_p` (0 to 1). A parameter you leave unset is not sent, so the provider's default applies. Anthropic requires `max_tokens`, so an unset value becomes 1024. |
| Source | Either a **prompt version** with a value for each of its variables, or **typed messages** (1 to 50, each with a role of `system`, `user` or `assistant`) that are sent as they are. |

A text prompt is sent as one `user` message. Choosing a version fills the model from its [model advice](/docs/prompts/) when the model field is empty; you can change it freely.

When a prompt needs variables, fill in their values for each configuration. A configuration whose variables are not all filled fails by itself (`missing_variables: question`) without calling the provider, and so does one whose filled-in prompt would exceed 256 KB (`prompt_too_large`).

Once you have a configuration you like, you can save it as a new version of a prompt. That is the ordinary "add a version" call, so a template identical to the latest version is refused.

## What a run does

- The calls go out at the same time, **one attempt each, with no retries and no fallbacks**, so what you see is what that credential and model do.
- **Answers are not streamed.** The page shows each answer when it is complete, and time to first token is "—".
- Each result shows the answer, the token usage, the duration and the cost. Cost and tokens are "—" when the model has no price or the provider returned no usage; unknown is never shown as 0.
- **One configuration failing does not affect the others.** Its column shows the error (a provider failure, a missing variable, or a project budget that blocks the call) and the other columns still show their answers.

## Where the traces go

Every configuration leaves a trace in the project:

- environment `playground`, trace name `playground`, tag `spanlight:playground`;
- the trace's user id is your account id;
- the span records the prompt version when you used one, so it appears in that version's [usage](/docs/prompts/#see-which-version-produced-a-trace).

Open a result's trace from the run to see the exact request and response. [Evaluators](/docs/evaluators/) never sample playground traces, so trying prompts does not trigger automatic scoring.

## Cost and limits

- **The playground has no spend cap of its own.** It is a person typing, and each call is bounded by `max_tokens`, which is at most 4096. The cost still counts: it appears in the project's spend and in any [budget](/docs/budgets/). A budget set to **block** stops the call, and the column shows `BUDGET_EXCEEDED`.
- **30 runs a minute per person.** A run is one request of up to three configurations. Over the limit the API answers `429 RATE_LIMITED` with `Retry-After`.
- It needs `CREDENTIALS_KEYS` to be set so credentials can be opened, and the gateway to be running in the API process; otherwise the API answers `409 NOT_CONFIGURED`.
- The prompts and answers are never written to Spanlight's logs. They exist as the trace's spans, and follow the project's payload capture and retention settings like any other trace.

## From the API

`POST /api/v1/projects/{project_id}/playground/runs` needs the `experiments:run` permission (members and above) and accepts an `Idempotency-Key`.

```json
{
  "configs": [
    {
      "credential_id": "0192f3a1-0000-7000-8000-000000000001",
      "model": "claude-haiku-5-5",
      "params": { "max_tokens": 300 },
      "source": {
        "prompt_version_id": "0192f3a1-5c3e-7b1a-9d2c-8e4f6a7b8c9d",
        "variables": { "question": "How do refunds work?" }
      }
    },
    {
      "credential_id": "0192f3a1-0000-7000-8000-000000000001",
      "model": "claude-haiku-5-5",
      "source": { "messages": [{ "role": "user", "content": "Say hello in French." }] }
    }
  ]
}
```

The answer has one result per configuration, in the same order:

```json
{
  "results": [
    {
      "output": "Refunds are issued to the original payment method within five days.",
      "usage": { "input_tokens": 212, "output_tokens": 31, "cached_tokens": null },
      "cost_usd": "0.0000367",
      "latency_ms": 842.1,
      "ttft_ms": null,
      "trace_id": "5b8efff798038103d269b633813fc60c",
      "span_id": "eee19b7ec3c1b174",
      "error": null
    },
    {
      "output": null,
      "usage": null,
      "cost_usd": null,
      "latency_ms": null,
      "ttft_ms": null,
      "trace_id": null,
      "span_id": null,
      "error": "BUDGET_EXCEEDED"
    }
  ]
}
```

`cost_usd` is a decimal string, or `null` when unknown. A credential that is not your organization's, or a prompt version that is not your project's, answers `404` for the whole request.
