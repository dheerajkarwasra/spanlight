---
title: Datasets
description: Build a set of test cases from your real traces or from a CSV or JSONL file, and learn the format, the limits and what gets rejected.
sidebar:
  order: 1
---

A **dataset** is a named list of test cases that you replay through a prompt in an [experiment](/docs/experiments/). Each **item** has an `input`, an optional `expected_output` and some `metadata`. Build one from real traces that went wrong or went well, or upload a file.

Anyone who can see the project can read datasets. **Members**, **admins** and **owners** can create, edit and delete datasets and their items. Datasets live under **Datasets** in the project.

## Datasets and items

- **A dataset** has a name (1 to 100 characters, unique in the project) and an optional description (up to 1000 characters). It holds up to **10 000 items**; adding more answers `422 DATASET_FULL` and stores nothing.
- **`input`** is required and can be any JSON value except `null`: a string, or an object whose string values fill a prompt's variables (see [experiments](/docs/experiments/#how-items-fill-the-target)).
- **`expected_output`** is optional JSON. It is `null` when there is none, never an empty value. Some evaluators (`exact_match`, `levenshtein`, and the faithfulness judge) compare the answer with it.
- **`metadata`** is a JSON object of your own notes, for example a category or a source.
- `input` and `expected_output` are at most 64 KB each and `metadata` at most 8 KB, measured as compact JSON. A larger value is rejected, never cut. A NUL character is removed from every string, because the database cannot store it.
- Items also remember where they came from (`source_trace_id`, `source_span_id`) when they were copied from a trace.

You can change an item's `expected_output` (send `null` to clear it) and replace its `metadata` as a whole. The `input` of an item cannot be changed; delete the item and add a new one.

**Deleting a dataset** removes its items. It is refused with `409 DATASET_IN_USE` while an experiment uses it.

## Add items from traces

The fastest way to a realistic dataset is to start from what your application actually received. On **Traces**, select traces and choose **Add to dataset**, or use the trace detail page.

For each trace Spanlight copies one item from a span:

- the span you name, or else the trace's **first LLM span** by start time;
- its stored input becomes the item's `input`, and its stored output becomes the item's `expected_output`;
- the item's metadata is `{trace_id, span_id, model, environment}`, and its source fields are set.

**The recorded output is only a starting point.** It is what your application said, not necessarily what it should have said. Edit the expected output of each item before you rely on it.

A trace that gives no item is listed in `rejected` with a reason and does not stop the others:

| Reason | Meaning |
| --- | --- |
| `trace not found` | The id is not a trace of this project |
| `no llm span` | The trace has no LLM span to copy |
| `span not found` | You named a span the trace does not have |
| `no payload stored` | The span has no stored input, because the project does not capture payloads |

You can add up to 200 traces in one request. The copied text is what was stored at ingestion, with secrets already redacted.

## Upload a file

Upload a `.jsonl` or `.csv` file from the dataset page, or `POST` it as `multipart/form-data` to `/api/v1/projects/{project_id}/datasets/{dataset_id}/items/upload`.

- **Format.** UTF-8 text (a byte order mark is removed). Any other extension or encoding answers `415 UNSUPPORTED_FILE`.
- **Size.** At most **5 MB**. A larger file answers `413 PAYLOAD_TOO_LARGE`.
- **A bad line never fails the file.** Each line or record that cannot be used is reported with its line number and a reason, and everything else is accepted. The reason says what is wrong ("not valid JSON", "input is required") and never repeats the content of the line. At most the first 100 problems are listed.
- Parsing stops once the file holds more items than a dataset can, and the upload is then refused as `DATASET_FULL`.

### JSONL

One JSON object per line. `input` is required, `expected_output` is optional, and every other key goes into `metadata` (an object under `metadata` is used as the base). Blank lines are skipped.

```jsonl
{"input": "How do refunds work?", "expected_output": "Refunds go back to the original payment method.", "topic": "billing"}
{"input": {"question": "Can I change my plan?", "tier": "pro"}}
{"input": "Where is my invoice?", "metadata": {"topic": "billing", "priority": 2}}
```

### CSV

The first row is a header and must contain an `input` column. `expected_output` is optional, and every other column goes into `metadata` as a string. Quoted commas and line breaks work, and blank rows are skipped.

```csv
input,expected_output,topic
"How do refunds work?","Refunds go back to the original payment method.",billing
"{""question"": ""Can I change my plan?"", ""tier"": ""pro""}",,account
```

A cell in `input` or `expected_output` that holds a JSON **object or array** is stored as that value; anything else is stored as text. In the second row above, `input` becomes an object. A record that cannot be read at all (for example a field that is never closed) is reported and ends the file there, because everything after it cannot be trusted.

## Add items by API

`POST .../datasets/{dataset_id}/items` takes `{"items": [{"input", "expected_output"?, "metadata"?}, ...]}` with 1 to 1000 items. `POST .../datasets/{dataset_id}/items/from-traces` takes `{"trace_ids": [...], "span_id"?}`. Both need `datasets:write` and answer `201` with `accepted` (and `rejected` for traces).

| Route (under `/api/v1/projects/{project_id}`) | Needs | Does |
| --- | --- | --- |
| `GET /datasets` | `project:read` | List; `name` returns the dataset with exactly that name |
| `POST /datasets` | `datasets:write` | Create (`409 NAME_TAKEN`) |
| `GET`, `PATCH`, `DELETE /datasets/{dataset_id}` | read, `datasets:write` | Read, change the description, delete |
| `GET /datasets/{dataset_id}/items` | `project:read` | Items by id, oldest first |
| `PATCH`, `DELETE /datasets/{dataset_id}/items/{item_id}` | `datasets:write` | Edit `expected_output` and `metadata`, or delete an item |

The dataset's **name** is its handle in the command line and in the [eval gate](/docs/eval-gate/): they look it up with `GET /datasets?name=...`.
