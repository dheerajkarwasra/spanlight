# 18. The content search index stays on the write path

Date: 2026-11-10 · Status: accepted

## Context

`q` on the trace list finds a trace when a span's content contains the text. A trigram index,
`spans_content_trgm_idx`, makes that fast over any window: it covers the last 2,000 characters
of each span's input and the first 2,000 of its output.

Measured through ingestion on 2026-11-10, the index is the largest cost of storing a span once
spans carry real content. For 100 spans with 2 kB prompts: 41 of the 46 ms of the insert, 3.7
of the 3.9 MB of write-ahead log, and an index about as large as the spans it covers (see
[performance](../performance.md#where-an-ingest-batch-spends-its-time-2026-11-10)).

Alternatives, measured on the same 12,000 spans, 100 per transaction:

| Index on the content | Insert, 100 spans | WAL, 100 spans | Index size |
| --- | --- | --- | --- |
| none | 5 ms | 0.3 MB | 0 |
| trigram, 2,000 + 2,000 characters (shipped) | 46 ms | 3.9 MB | 34 MB |
| trigram, 1,000 + 1,000 | 30 ms | 2.9 MB | 26 MB |
| trigram, 500 + 500 | 21 ms | 2.2 MB | 21 MB |
| full text (`simple`), 2,000 + 2,000 | 25 ms | 1.7 MB | 16 MB |
| GiST trigram signatures | 38 ms | 1.0 MB | 52 MB |

Two more facts decide most of it:

- Building the same trigram index in bulk over those spans took 6.1 s, 51 ms per 100 spans: no
  less than adding them one batch at a time. Extracting the trigrams alone is more than half of
  the work.
- A larger GIN pending list, `fastupdate = off` and WAL compression were tried in the ingest
  measurements; none kept the gain it promised.

## Decision

Keep the trigram index as it is, written in the ingest transaction.

- **Not built later by a worker.** The work is the same whenever it is done, so a deferred index
  would move the cost and not remove it, and would add a second copy of the text or a second
  version of every span row, a lag before new content is searchable, and one more thing to
  delete under retention and erasure.
- **Not full-text.** It halves the cost, but it matches whole words: `timeout` would no longer
  find `ReadTimeoutError`, and a fragment of an id or a URL would find nothing. Error strings
  and identifiers are what people paste into this search.
- **Not less text.** Halving the indexed text saves a third of the insert time and a quarter of
  the index, and makes the search miss content it finds today.
- **No switch to turn it off.** No deployment has asked for one, and every switch is a second
  behaviour to keep correct.

## Consequences

- Budget about half a millisecond of database time and 40 kB of write-ahead log per span with
  content, and plan disk as roughly twice the size of the stored spans.
- Revisit when a real project's ingestion is limited by the database's CPU or disk, not before.
  The first step then is a deployment setting that leaves new spans out of the index (a partial
  index on a flag); search already falls back to the window's last hour when it cannot use the
  index (`app.explorer.content_search`).
