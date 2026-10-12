# Seed the demo evaluations

## Purpose and when to use

The hosted demo shows the evaluation screens (prompts, datasets, evaluators, experiments) with real content. `spanlight demo seed-evals` creates it once, from the demo bot's own recorded traffic. Use it after the demo project has collected traces, or after you cleared the seeded objects and want them back. Self-hosters do not need it.

It creates, in the demo project:

| Object | Name | Content |
| --- | --- | --- |
| Prompt | `support/answer` | Two chat versions. Version 1 is the answer prompt the demo bot uses today; version 2 is a tightened rewrite (below). Label `production` points at version 2 and `staging` at version 1. |
| Dataset | `support-questions` | 50 items, one per recorded demo answer call: the newest 50 `llm` spans that answer a customer and have a stored payload. The item input is the call's user message (the FAQ entry and the customer question); the recorded answer is kept as the expected output. |
| Evaluator | `relevance` | An LLM judge on the built-in `relevance` template (prompt `judges/relevance`), model `claude-haiku-5-5`, sampling 5 % of traces, monthly budget $0.50. |
| Experiment | `answer-v1-vs-v2` | The dataset replayed through `support/answer` on `claude-haiku-5-5`, scored by `relevance`, 50 items and a $0.50 cap per run. Its target is version 1; the second run overrides the target with version 2, so the two runs compare the versions. |

An experiment runs one run at a time. The first call of the command queues the version 1 run; once it has ended, a second call queues the version 2 run.

### The two prompt versions

Both versions are a chat prompt: a system message and a user message `{{input}}`.

Version 1 (system message):

> You are a concise, friendly support agent. Answer the customer in at most three sentences using only the FAQ entry provided. If it does not cover the question, say a human will follow up.

Version 2 (system message):

> You are a friendly, concise support agent. Answer the customer in at most two sentences, using only the FAQ entry provided and putting the answer first. Do not add facts the entry does not contain. If the entry does not cover the question, say in one sentence that a human will follow up.

## Prerequisites

- The worker has run the demo traffic job long enough for 50 answered conversations with stored payloads. Check the count below.
- An Anthropic credential on the demo organization. The worker creates it (`demo-anthropic`) when `ANTHROPIC_API_KEY` and `CREDENTIALS_KEYS` are set; without one the command stops with `demo org has no Anthropic credential`.
- A worker running afterwards: the command only queues the runs, the worker executes them.
- Docker Compose shortcut:

  ```bash
  spl() { docker compose -f deploy/compose.yaml --env-file deploy/.env "$@"; }
  ```

Spend: each experiment run is capped at $0.50 and the judge at $0.50 a month, so the seed adds about $1.50 at most, and about $0.50 a month afterwards from the judge's 5 % sample. The existing demo traffic and explanation caps are unchanged. Repeating the command once both runs exist spends nothing.

## Check the traffic

```bash
spl exec postgres psql -U postgres -d spanlight -c \
  "SELECT count(*) AS answers FROM spans s
     JOIN projects p ON p.id = s.project_id JOIN organizations o ON o.id = p.org_id
    WHERE o.slug = 'demo' AND s.kind = 'llm' AND s.output IS NOT NULL
      AND s.input->'messages'->0->>'content' LIKE 'FAQ entry:%'"
```

Healthy: `answers` is 50 or more.

## Run it

```bash
spl exec api spanlight demo seed-evals
```

Expected output on the first run (ids differ):

```text
Seeded the demo evaluations.
  prompt: 0192f3a1-...
  dataset: 0192f3a1-...
  evaluator: 0192f3a1-...
  experiment: 0192f3a1-...
  run v1: 0192f3a1-... (queued)
  dataset items: 50
The v2 run waits for the v1 run to end; run this command again then.
```

When the v1 run has ended (done or failed), run the command again. It queues the version 2 run:

```text
Seeded the demo evaluations.
  run v2: 0192f3a1-... (queued)
  dataset items: 50
```

A third call prints `already_seeded: nothing changed (50 dataset items).` An object that exists by name is kept as it is, so edits you made survive, and a run is queued only when the experiment has fewer than two.

With fewer than 50 answers the command exits 1 and writes nothing:

```text
Error: need 50 traces, found 10
```

Wait for more demo traffic and run it again. Without an Anthropic credential it exits 1 with `Error: demo org has no Anthropic credential`.

## Check that it worked

Open the demo project in the dashboard:

- Prompts: `support/answer` with two versions, `production` on version 2.
- Datasets: `support-questions` with 50 items.
- Experiments: `answer-v1-vs-v2` with two runs, each moving from queued to running to done in a few minutes. Open the experiment and compare its two runs to see version 1 against version 2. A run that ends `failed` with `cost_cap_reached` stopped at its $0.50 cap; its stored items stay.

## Re-seed from scratch

Seeding keeps what exists. To rebuild after a change to the recorded traffic or to the prompts:

1. Wait until the runs have finished (a deleted run whose job is still queued fails that job).
2. Delete the experiment. There is no delete for experiments in the dashboard, so remove it as the database owner; its runs and items go with it:

   ```bash
   spl exec postgres psql -U postgres -d spanlight -c \
     "DELETE FROM experiments
       WHERE name = 'answer-v1-vs-v2'
         AND project_id = (SELECT p.id FROM projects p JOIN organizations o ON o.id = p.org_id
                            WHERE o.slug = 'demo' AND p.slug = 'support-bot')"
   ```

3. In the dashboard delete the dataset `support-questions`, the evaluator `relevance` and then the prompt `support/answer` (a dataset or prompt in use is refused until the experiment and the evaluator are gone). The prompt `judges/relevance` can stay; the next seed reuses it.
4. Run the command, then once the v1 run has ended run it again for the v2 run.

## Related

- [detectors.md](detectors.md): the explanation budget that shares the demo's Anthropic spend.
- [gateway.md](gateway.md): the credential the seed reads.
