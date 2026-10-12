# Spanlight eval gate

A composite GitHub Action that runs a prompt version over a Spanlight dataset, scores every answer with your evaluators and fails the job when a mean score is below the threshold you set.

```yaml
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

Each run is a real run in Spanlight with real provider calls. The job exits `0` when every threshold holds, `1` when a score is below its threshold and `2` when the run could not be set up, failed or timed out. A Markdown table of the scores is printed to the log and added to the job summary.

## Inputs

| Input | Required | Default | Meaning |
| --- | --- | --- | --- |
| `host` | yes | | URL of your Spanlight instance. |
| `token` | yes | | Personal access token (`spl_pat_...`) with the write scope. Keep it in a secret. |
| `project-id` | yes | | Id of the project that owns the dataset and the prompt. |
| `dataset` | yes | | Dataset name. |
| `prompt` | yes | | `name`, `name@label` or `name:version`; a bare name means the `production` label. |
| `evaluators` | no | | Comma-separated evaluator names. |
| `fail-below` | no | | `score=minimum` pairs, for example `relevance=0.8,format=1`. Empty means report only. |
| `credential-id` | yes | | Provider credential to run the prompt on. |
| `model` | yes | | Model to run the prompt on. |
| `max-cost-usd` | no | | Cost cap of one run in US dollars; used only when the experiment is created. |
| `python-version` | no | `3.12` | Python used to run the gate (3.10 or newer). |
| `sdk-ref` | no | the action's ref | Git ref of the Spanlight repository to install the SDK from. Empty installs the SDK from the same tag or commit the action is pinned to (`main` only when that ref is unknown). |

## Outputs

| Output | Meaning |
| --- | --- |
| `run-id` | Id of the experiment run. |
| `run-url` | Link to the experiment in the dashboard. |

The experiment is named `ci/<dataset>/<prompt>/<id>`, where `<id>` is a short digest of the evaluators, credential and model (an experiment cannot be edited, so a change starts a new one). It is created on first use and reused afterwards, and every run names the prompt version it tests, so a label that moves to a new version runs the new version in the same experiment. The gate waits for an earlier run of the same experiment to finish, so add a `concurrency:` group to your workflow. Within one workflow attempt a retried request starts only one run. Every Spanlight instance serves the full description at `/docs/eval-gate/`.
