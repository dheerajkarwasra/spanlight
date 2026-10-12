---
title: Scores and review queues
description: Attach quality scores to traces from your code, by hand or with evaluators; define score configs; filter traces by score; and review traces with annotation queues and keyboard shortcuts.
sidebar:
  order: 1
---

A **score** is a judgement about a trace, or about one span in it: "helpfulness 0.8", "resolved: yes", "tone: friendly". Scores turn "the answer looked fine" into something you can filter, chart and compare between prompts.

Every score has a **name**, a **value** and a **source**:

| Source | Written by |
| --- | --- |
| `api` | Your code, through `spanlight.score(...)` or `POST /v1/scores` |
| `human` | A person, on a trace's detail page or in a review queue |
| `eval` | An [evaluator](/docs/evaluators/) or an [experiment](/docs/experiments/) |

A score may also carry a comment (up to 2000 characters). A trace can have many scores, including several with the same name; wherever Spanlight needs one value per name it uses the **latest**.

## Score types and configs

A score's value is one of three types:

| Type | Value | Stored as |
| --- | --- | --- |
| `numeric` | A number from -1 000 000 000 to 1 000 000 000, kept to four decimals | The number |
| `boolean` | `true` or `false` | `1` or `0`; the dashboard shows Yes and No |
| `categorical` | A string of 1 to 200 characters | The string |

Score values are JSON numbers and booleans, not strings. A score sets exactly one of `value` and `string_value`.

A **score config** says what a name means: its type, an optional range for numbers (`min_value`, `max_value`, either may be open), an optional list of allowed categories (empty means any), and a description. Names are 1 to 64 lowercase letters, digits, `_`, `.` or `-`, starting with a letter or digit.

- **You do not have to define a config first.** The first score with a new name creates an **implicit** config with no limits and the type its value implies: a string is categorical, `true`/`false` is boolean and any other number is numeric. Later scores are checked against it.
- **Define a config to constrain a name.** Create or edit one under **Settings, Project, Score configs**, or with `POST /score-configs`. Editing an implicit config makes it explicit. Creating a config for a name that already has one, implicit or not, answers `409 NAME_TAKEN`; edit the existing one instead.
- **Types are fixed once scores exist.** Changing a config's type, or deleting it, answers `409 SCORE_CONFIG_IN_USE` while any score carries the name. Deleting a config a review queue asks for answers the same. A new range or category list applies to new scores only; stored scores are not changed.
- **A project has at most 500 score names.** Names are meant to be a fixed vocabulary. Once a project has 500 configs, a score with a new name is rejected (`name: too many score names in this project (at most 500)`) and so is creating a config; scores for existing names are unaffected.

A score that does not fit its config is rejected with the field and the reason:

| Problem | Reason |
| --- | --- |
| Neither or both of `value` and `string_value` | `exactly one of value and string_value is required` |
| Number outside the range | `value: must be between 0 and 1` (or `at least` / `at most`) |
| Category not in the list | `string_value: must be one of pass, fail` |
| Boolean config, other number | `value: must be 0, 1, true or false` |
| Wrong type for the name | `data_type: score "relevance" is numeric` |
| Name not allowed | `name: must be 1 to 64 lowercase letters, digits, '_', '.' or '-', starting with a letter or digit` |
| A new name past the project's 500 | `name: too many score names in this project (at most 500)` |

NUL characters are removed from `string_value` and `comment` before they are checked.

Scores follow the project's retention: a score is deleted once it is older than the project's `retention_days`, like the traces it describes. A comment or a judge's reasoning can quote the trace, so it never outlives it. Review-queue items are deleted on the same schedule.

## Send scores from your code

### With the Python SDK

```python
import spanlight

trace_id = spanlight.get_current_trace_id()
spanlight.score(trace_id, "helpfulness", 0.8)
spanlight.score(trace_id, "resolved", True, comment="user said thanks")
spanlight.score(trace_id, "tone", "friendly", span_id=span.span_id)
```

A number makes a numeric score, `True` or `False` a boolean one and a string a categorical one. Scores travel in the background with the spans, after the spans of the same batch, and share the SDK's queue. Each call gets a unique id, so a retried request never stores a score twice. `score` never raises. A server that predates scores answers `404`; the SDK logs it once and drops scores.

### With HTTP

`POST /v1/scores` takes up to 1000 scores. It needs a project API key with the **`scores:write`** scope; a key made before this scope existed cannot send scores, so create a new key that has it (see [API keys](/docs/guides/access/)). The request limit is shared with ingestion (50 a second, bursts of 100 per key), and gzip or deflate bodies are accepted like on `/v1/traces`.

```bash
curl -X POST https://spanlight.example.com/v1/scores \
  -H "Authorization: Bearer $SPANLIGHT_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"scores": [
        {"id": "0192f3a1-5c3e-7b1a-9d2c-8e4f6a7b8c9d",
         "trace_id": "5b8efff798038103d269b633813fc60c",
         "name": "helpfulness", "value": 0.8, "comment": "clear and short"},
        {"trace_id": "5b8efff798038103d269b633813fc60c",
         "name": "resolved", "value": true}
      ]}'
```

```json
{ "accepted": 2, "rejected": [] }
```

| Field | Meaning |
| --- | --- |
| `trace_id` | The trace, 32 hex characters. It does not have to exist yet: a score can arrive before its trace. |
| `span_id` | Optional. Scores a span instead of the whole trace. |
| `name`, `value` / `string_value`, `comment` | As above. |
| `id` | Optional UUID. Send one so a retry cannot store the score twice. |

- **Per-score results.** A score that fails validation is listed in `rejected` as `{index, reason}` and never fails the others. The request fails only when the body is empty or has more than 1000 scores (`422`).
- **Retries are safe.** Ids are unique per project, so another project using the same id does not interfere. A repeated id counts as accepted and stores nothing new.

## Score by hand

Open a trace and add a score from its detail page: choose one of the project's score configs (a score sent from the SDK creates its config automatically, or define one in **Settings, Project**), give a value and an optional comment. The trace must exist in the project (`404` otherwise). Members and above can add scores (`scores:write`). A **human** score is attributed to its author.

You can delete a human score you added. Admins and owners can delete any score. Evaluator scores and API scores can only be deleted by an admin.

## Find traces by score

The trace detail lists every score of the trace, latest first, and **Traces** shows each trace's latest score per name (up to ten names per trace) in a Scores column. Filter the list with:

| Parameter | Keeps traces where |
| --- | --- |
| `score_name` | the trace has a score with this name |
| `score_min`, `score_max` | the **latest** score with that name is a number in the range |
| `score_value` | the latest score with that name is this category |
| `has_scores` | the trace has any score (`true`) or none (`false`) |

`score_min`, `score_max` and `score_value` only make sense with a name: sending them without `score_name` answers `422 SCORE_FILTER_NEEDS_NAME`. A boolean score is a number, so filter "resolved" with `score_name=resolved&score_min=1`. "Latest" means the highest creation time, with ties broken by the higher id.

## Review queues

An **annotation queue** is a to-do list of traces for people to score. Create one under **Review** in the project: give it a name (unique in the project, up to 100 characters), instructions for the reviewers (up to 4000 characters) and one to 20 score configs. Reviewers give **one score for every config of the queue**, so a queue with "relevance" and "tone" asks for both each time.

- **Add traces** from the Traces page (select traces, then **Add to queue**) or by API (`POST .../annotation-queues/{queue_id}/items`, up to 200 traces, optionally one span of each). A trace already in the queue is skipped. If any trace is not in the project, the whole batch is refused (`422`) and nothing is queued.
- **Review** with **Start reviewing**. Spanlight hands you the next item: the oldest pending item that is free, already yours, or claimed by someone else more than 30 minutes ago, so an abandoned claim cannot strand the queue. Two reviewers never get the same item at once.
- **Finish** an item by scoring it, or **skip** it (it stays in the queue as skipped), or **release** it back to the queue. Scoring stores the scores with source `human`, attributed to you, and marks the item done in one step. Only the person holding an item can complete, skip or release it (`409 ITEM_NOT_ASSIGNED_TO_YOU`), and an item that is already done or skipped answers `409 ITEM_NOT_PENDING`. A score that is not one of the queue's configs is `422 SCORE_NOT_IN_QUEUE` and a missing one `422 SCORE_REQUIRED`.
- **Progress** shows pending, done and skipped counts. Deleting a queue deletes its items and keeps the scores already given.

Members, admins and owners can create queues and review (`annotations:write`); viewers can look at progress.

### Keyboard shortcuts

The review screen is built to be used without the mouse. A key does nothing while you are typing in the comment field.

| Key | Does |
| --- | --- |
| `1` to `9` | Pick that option of a category or yes/no score |
| `0` to `9` | Type a value when a number score has focus |
| `Enter` | Submit the scores and open the next item |
| `S` | Skip this item |
| `N` | Claim the next item |
| `?` | Show or hide the list of shortcuts |
| `Esc` | Close the list, then leave the review |

## Routes

Under `/api/v1/projects/{project_id}`, except `POST /v1/scores`.

| Route | Needs | Does |
| --- | --- | --- |
| `POST /v1/scores` | API key with `scores:write` | Ingest scores |
| `GET`, `POST /score-configs` | read, `scores:write` | List and create configs |
| `PATCH`, `DELETE /score-configs/{config_id}` | `scores:write` | Edit and delete a config |
| `GET /traces/{trace_id}/scores` | `project:read` | A trace's scores, latest first |
| `POST /traces/{trace_id}/scores` | `scores:write` | Add a human score |
| `DELETE /scores/{score_id}` | `scores:write` | Delete (yours, or any as an admin) |
| `GET`, `POST /annotation-queues`, `GET`, `DELETE /annotation-queues/{queue_id}` | read, `annotations:write` | Queues |
| `POST`, `GET /annotation-queues/{queue_id}/items` | `annotations:write`, read | Queue traces, list items (`status` filters) |
| `POST /annotation-queues/{queue_id}/claim` | `annotations:write` | Next item, or `204` when none |
| `POST .../items/{item_id}/complete`, `/skip`, `/release` | `annotations:write` | Finish an item |

Completing takes `{"scores": [{"name", "value" or "string_value", "comment"?}]}`.
