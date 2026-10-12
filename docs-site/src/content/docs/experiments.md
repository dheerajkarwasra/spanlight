---
title: Experiments
description: Replay a dataset through a prompt version or typed messages, score every answer with evaluators, and compare two runs item by item. Targets, the cost limit, how evaluators are budgeted, and the demo seed.
sidebar:
  order: 1
---

An **experiment** answers "is this prompt better than that one?" with your own test cases. It joins a [dataset](/docs/datasets/), a **target** (a prompt version or typed messages on one credential and model) and some [evaluators](/docs/evaluators/). Each **run** sends every dataset item through the target, scores each answer, and records the answers, scores, time and cost. Then you compare two runs side by side.

**Every run makes real provider calls and spends money.** Members, admins and owners can create experiments and start runs; viewers can read them. Experiments live under **Experiments** in the project. To run the same comparison on every pull request, use the [eval gate](/docs/eval-gate/).

## Create an experiment

| Part | Meaning |
| --- | --- |
| Name | 1 to 200 characters, unique in the project |
| Dataset | The items to replay |
| Target | What answers each item (below) |
| Cost limit (`max_cost_usd`) | The most one run may spend on the target's calls. From above 0 up to 1000 dollars; 5 by default |
| `max_items` | How many dataset items a run reads. 1000 by default, and never more than the server's `EXPERIMENT_MAX_ITEMS` |
| Evaluators | Up to 20 evaluators that score every answer |

An experiment cannot be edited or deleted after it is created. Its dataset, target, evaluators and limits are fixed, so every run of it is comparable. To change them, make a new experiment.

### Targets

A target names a **credential** of your organization (any provider), a **model** and optional parameters (`temperature`, `max_tokens` up to 4096, `top_p`). The model must have a price, because the cost limit could not be enforced without one (`422 EXPERIMENT_MODEL_UNPRICED`). There are two kinds:

- **Prompt version.** A stored [prompt](/docs/prompts/) version, filled in with each item. This is how you test a prompt change.
- **Messages.** Typed `system`, `user` and `assistant` messages, sent unchanged for every item. The item's input is not used. This tests a model or a parameter against fixed instructions.

### How items fill the target

For a prompt target, the item's `input` fills the template's variables:

- an `input` that is a JSON object whose values are all strings supplies the variables by name (`{"question": "..."}`);
- any other input becomes the single variable `input`, as text.

An item that cannot fill every variable the template uses gets the item error `missing_variables: question, tier`, and one whose filled-in prompt would exceed 256 KB gets `prompt_too_large`. That fails that item only, not the run.

## Start a run

`POST /api/v1/projects/{project_id}/experiments/{experiment_id}/runs` queues a run and answers `202`; a worker executes it. Send an `Idempotency-Key` so a retry cannot start a second run (the body is part of what a retry must repeat).

- **One run at a time.** While a run is queued or running, another start answers `409 RUN_IN_PROGRESS`.
- **Compare prompt versions with one experiment.** The body may be `{"target_override": {"prompt_version_id": "..."}}`. For a prompt-version experiment, that runs another version of **the same prompt**, with the experiment's credential, model and parameters. Anything else answers `422 TARGET_OVERRIDE_INVALID`. Every run keeps the effective target it used, so one experiment can hold a run for version 1 and a run for version 2.
- **Numbering.** Runs are numbered 1, 2, 3 in the order they start, and a number never changes.

## What a run does

1. Reads the dataset's items in order, up to `max_items`, with **four calls in flight** at a time.
2. Sends each item to the target. Each call is a trace in environment `experiments`, named `experiment:<experiment name>` and tagged `spanlight:experiment:<run id>`. A prompt target links the span to its prompt version.
3. Scores the answer with each attached evaluator. A heuristic evaluator and a judge receive the item's `expected_output`, if it has one. Each score is stored on the item and also written on the item's trace (source `eval`), under the evaluator's name.
4. Stores the item: the answer (`text` and `finish_reason`), the duration, the cost, the scores, and an error if there was one.

When the run ends, its **totals** are worked out from the stored items:

| Total | Meaning |
| --- | --- |
| Items, errors | How many items ran and how many ended in an error |
| Cost | The sum of the items whose cost is known. If any call has no price or no usage, the cost is a lower bound and is flagged as such |
| Latency p50, p95 | Nearest-rank percentiles over the answered items |
| Scores | Per score name: the mean and count of a numeric or yes/no score (yes counts 1), or the count per category |

Unknown values are shown as "—", never 0.

### The cost limit

The cost limit protects you from a run that is larger than you thought. Before it sends an item, a run checks that **what has been spent, plus the estimates of the calls in flight, plus this item's estimate** stays within `max_cost_usd`. The estimate is a ceiling made from the prompt, the model's price and the output limit. When the next item would pass the limit, the in-flight calls finish, the check is repeated with their real costs, and if it still fails the run ends `failed` with `cost_cap_reached`. The UI says "Stopped: cost limit reached". The items already stored stay, so a stopped run is still readable.

**The limit covers the target's calls only.** Evaluators spend under their own budgets: a judge draws on its [monthly budget](/docs/evaluators/#the-monthly-budget), which this experiment shares with everything else that judge does that month. A judge that is out of budget leaves that item's score out; it does not fail the item or the run. So the most an experiment can spend is its cost limit plus what its judges have left in their budgets.

### Run states and errors

A run is `queued`, `running`, `done` or `failed`. A failed run says why in `error`:

| Error | Meaning |
| --- | --- |
| `cost_cap_reached` | The cost limit was reached; stored items stay |
| `abandoned` | The worker died or stopped sending its heartbeat for 10 minutes |
| `cancelled` | The worker was shut down or timed out while the run was going |
| `model_unpriced` | The model lost its price, so the limit could not be enforced; nothing was called |
| `credential_missing`, `credentials_not_configured`, `prompt_version_missing`, `experiment_config_invalid` | The run could not start; nothing was called |
| `internal_error`, `lease_lost` | A failure on Spanlight's side; the run stopped |

An item has its own `error`: `missing_variables: ...`, `BUDGET_EXCEEDED` when a project budget set to block stopped the call, or the provider's failure. It may also carry an `evaluator_error` when scoring itself broke; the answer and its cost are kept.

## Compare two runs

Choose two runs of an experiment and open **Compare**. Both must have finished; comparing a run that is queued or running answers `409 RUN_IN_PROGRESS`.

- **Deltas are B minus A.** For a numeric score (a yes/no score counts 1 and 0), the delta is `b - a` to four decimals, per item and for the whole run.
- **Higher is better.** Every score Spanlight produces, built-in judges included, is higher-is-better, so a positive delta is **improved** and a negative one **regressed**. A score where lower is better is not supported; the page says so. Improved and regressed always carry an icon and a word as well as a colour.
- **Categories** have no delta: equal values are unchanged, different ones count as neither improved nor regressed.
- **Items match on the dataset item.** Items present in both runs come first; items that only one run has are listed after them with the other side empty and no deltas.
- **Cost and p95 duration** show the difference between the runs. When either side's cost is unknown, the cost difference is "—" rather than a guess.

The page also states the trade-off in a sentence built from the differences, for example "B scores +0.08 on relevance, costs +$0.012, p95 latency +310 ms."

## The demo seed

The hosted demo ships a ready-made experiment. `spanlight demo seed-evals` creates, in the demo project:

- the prompt `support/answer` with two versions (the demo bot's own prompt, and a tightened rewrite), with `production` on version 2 and `staging` on version 1;
- the dataset `support-questions`, with 50 items taken from the demo's own recorded answers;
- the judge `relevance`, on the built-in template, model `claude-haiku-5-5`, sampling 5 % of traces, with a monthly budget of $0.50;
- the experiment `answer-v1-vs-v2`, with a $0.50 cost limit per run and two runs: version 1, then version 2 through `target_override`.

It uses the demo organization's Anthropic credential, so it needs `ANTHROPIC_API_KEY`. Because an experiment runs one run at a time, you run the command once to queue version 1 and again, after that run ends, to queue version 2. It is safe to repeat: what exists is kept. The steps, the prompts and how to re-seed are in the [demo evaluations runbook](/docs/runbooks/demo-evals/).

## Routes

Under `/api/v1/projects/{project_id}/experiments`.

| Route | Needs | Does |
| --- | --- | --- |
| `GET`, `POST` | `project:read`, `experiments:run` | List (with run count and latest run) and create (`409 NAME_TAKEN`) |
| `GET /{experiment_id}` | `project:read` | One experiment |
| `POST /{experiment_id}/runs` | `experiments:run` | Start a run |
| `GET /{experiment_id}/runs` | `project:read` | Runs, newest first, with the totals stored when each ended |
| `GET /{experiment_id}/runs/{run_id}` | `project:read` | One run; totals are computed live while it runs |
| `GET /{experiment_id}/runs/{run_id}/items` | `project:read` | The run's items in dataset order |
| `GET /{experiment_id}/runs/{run_a}/compare/{run_b}` | `project:read` | The comparison, B minus A |

A create body is `{"name", "dataset_id", "config": {"target", "max_cost_usd"?, "max_items"?}, "evaluator_ids"?}`. A `404` means the dataset, an evaluator or the prompt version is not in the project, or the credential is not in the organization.
