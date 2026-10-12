---
title: Eval gate for CI
description: Run a prompt over a dataset on every pull request with the eval-gate GitHub Action and fail the build when a score drops below a threshold.
sidebar:
  order: 1
---

The **eval-gate** GitHub Action runs a prompt version over a Spanlight dataset, scores every answer with your evaluators and fails the job when a mean score is below the threshold you set. Use it to catch a prompt change that makes answers worse before it reaches users.

Each run is a real run in Spanlight: it appears under **Experiments**, every answer is a trace in the `experiments` environment, and every call is a real provider call that costs money. Each run is capped by the experiment's cost limit and `max_items` (see below).

## Before the first run

You need, in the project that holds the prompt:

1. A **dataset** with items and a **prompt** (and, if you want scores, **evaluators**). Judge evaluators need a monthly budget; a judge that is over budget leaves the score out and the gate fails, because an unknown score never counts as a pass.
2. A **personal access token** with the write scope. Create it as described in [Access](/docs/guides/access/) and store it as a repository secret, for example `SPANLIGHT_TOKEN`. Starting a run needs write access because a run spends money.
3. The **credential id** and **model** the prompt should run on. Credentials are listed under **Gateway, Credentials**.
4. The **project id**, the second id in the dashboard URL (`/<organization id>/<project id>/...`).

## Use it in a workflow

```yaml
name: Prompt evals
on:
  pull_request:
    paths: ['prompts/**']

# An experiment runs one run at a time: queue gates instead of running them side by side.
concurrency:
  group: spanlight-eval-gate-${{ github.ref }}

jobs:
  eval-gate:
    runs-on: ubuntu-latest
    steps:
      - uses: dheerajkarwasra/spanlight/.github/actions/eval-gate@main
        with:
          host: ${{ vars.SPANLIGHT_HOST }}
          token: ${{ secrets.SPANLIGHT_TOKEN }}
          project-id: ${{ vars.SPANLIGHT_PROJECT_ID }}
          dataset: support-questions
          prompt: support/answer@production
          evaluators: relevance,format
          fail-below: relevance=0.8,format=1
          credential-id: ${{ vars.SPANLIGHT_CREDENTIAL_ID }}
          model: claude-haiku-5-5
          max-cost-usd: '2'
```

Pin the Action to a tag or a commit in your own workflows (`@<tag>` after the Action path) so a change to the repository cannot change your gate. The SDK is installed from the same ref unless you set `sdk-ref`.

### Inputs

| Input | Required | Meaning |
| --- | --- | --- |
| `host` | yes | URL of your Spanlight instance. |
| `token` | yes | Personal access token with the write scope. |
| `project-id` | yes | Id of the project that owns the dataset and the prompt. |
| `dataset` | yes | Name of the dataset to run. |
| `prompt` | yes | `name`, `name@label` or `name:version`. A bare name means the `production` label; `latest` is the highest version. |
| `evaluators` | no | Comma-separated evaluator names. |
| `fail-below` | no | `score=minimum` pairs, for example `relevance=0.8,format=1`. Empty means the Action only reports. |
| `credential-id` | yes | Provider credential to run the prompt on. |
| `model` | yes | Model to run the prompt on. |
| `max-cost-usd` | no | Cost cap of one run, in US dollars. Applies only when the experiment is created; empty means the server default (5 USD). |
| `python-version` | no | Python to run the gate with. Default `3.12`. |
| `sdk-ref` | no | Git ref of the Spanlight repository to install the SDK from. Default: the ref the Action is pinned to, or `main` when that is unknown. |

Outputs: `run-id` and `run-url` (a link to the experiment in the dashboard).

## What the gate does

1. Finds the dataset, the prompt version and the evaluators by name. A name that does not exist stops the job with exit code 2.
2. Reuses the experiment `ci/<dataset>/<prompt>/<id>`, or creates it on first use with the resolved prompt version as its base target. `<id>` is a short digest of the evaluators, credential and model, because an experiment cannot be edited: change one of them and the gate starts a new experiment instead of running under the old settings. Every run names the version it tests, so when `production` moves to a new version the same experiment runs the new one and its runs stay comparable. The name can be at most 200 characters, so keep dataset and prompt names short.
3. Waits, within the timeout, until the experiment has no run in progress (it allows one at a time), then starts a run and follows it. Inside GitHub Actions the run is started with an `Idempotency-Key` made from `GITHUB_RUN_ID`, `GITHUB_RUN_ATTEMPT` and the experiment id, so a request that is retried within one attempt cannot start two runs. A re-run of the workflow is a new attempt and starts a new run.
4. Compares each mean score in `fail-below` with its threshold, prints a Markdown table to the log and adds it to the job summary.

| Exit code | Meaning |
| --- | --- |
| `0` | The run finished and every threshold held. |
| `1` | A score is below its threshold, for example `relevance: 0.75 < 0.8`. A score the run did not produce also counts as below. |
| `2` | The run could not be set up, failed (for example `cost_cap_reached`) or did not finish within 30 minutes. |

A run has a cost cap (`max-cost-usd`, 5 USD by default) and reads at most 1 000 dataset items by default. When the cap is reached the run ends `failed` and the gate exits with 2.

## Run it outside GitHub

The Action is a thin wrapper around a module of the Python SDK, so the same gate runs in any CI:

```bash
pip install "spanlight @ git+https://github.com/dheerajkarwasra/spanlight@main#subdirectory=sdks/python"

export SPANLIGHT_TOKEN=spl_pat_...
python -m spanlight._experiments \
  --host https://spanlight.example.com --project-id <project id> \
  --dataset support-questions --prompt support/answer:3 \
  --evaluators relevance,format --fail-below relevance=0.8,format=1 \
  --credential-id <credential id> --model claude-haiku-5-5
```

`--max-cost-usd` sets the cap of a new experiment and `--timeout` (seconds, default 1800) bounds the whole gate, including the wait for an earlier run. The token is read only from the environment so it never shows in a process list, and the runner refuses to send it to an `http://` host other than localhost.

## Limits

- Scores that are categories (a label rather than a number) cannot be compared with a threshold; use a numeric or boolean evaluator for the gate.
- The Action runs one experiment per step. To gate several datasets, add one step for each.
- Two gates for the same dataset, prompt and settings take turns: the second waits for the first to finish, and fails with exit code 2 if that takes longer than the timeout.
- Experiments cannot be edited or deleted yet, so an unused `ci/...` experiment stays in the list.
