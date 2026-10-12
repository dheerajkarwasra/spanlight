# 14. Immutable prompt versions, mutable labels

Date: 2026-10-10 · Status: accepted

## Context

Spanlight stores prompts as named templates in a project, and an application fetches one at run time by label (`production`, `staging`) or by version number. An LLM span can carry the id of the version that produced it, so a trace shows the exact template behind a call, and usage, cost and latency can be grouped by version. Experiments and LLM-judge evaluators also reference versions.

All of that holds only if a version id always means the same text. If a version could be edited in place, a span recorded last week would show this week's template, two experiment runs against "the same" version could have used different prompts, and a judge's score history would silently mix rubrics. At the same time, teams need to move fast: promote a version to production, roll back, try a candidate in staging, without copying templates around.

Two models were considered. **Editable prompts with an audit trail** keep one mutable row and log changes; every reader that needs the old text has to reconstruct it from the log, and nothing stops a change from being written outside the API. **Append-only versions with movable pointers** keep every template as its own row and put the "which one is live" decision in a small separate table.

## Decision

Versions are immutable at the database level; labels are mutable pointers.

- **A version never changes.** `prompt_versions` rows are written once. A trigger (`prompt_versions_immutable`) refuses every `UPDATE`, so the rule holds for a raw SQL session, a future code path or a bug, not only for the absence of an edit route. The single exception is `created_by` becoming NULL alone, which is what `ON DELETE SET NULL` does when the author's account is deleted; without it, deleting that user would fail.
- **Every edit is a new version.** Saving a changed template or model configuration creates version `n + 1`, numbered under a row lock on the prompt so concurrent saves get consecutive numbers. Saving one identical to the latest version is refused (`409 NO_CHANGES`), so accidental double saves do not create noise. A prompt's kind (`text` or `chat`) is fixed by its first version.
- **Labels move freely.** `prompt_labels` maps `(prompt, label)` to one version. Moving a label is an update of that small row; it is audited, and it changes what `GET /v1/prompts/{name}?label=` returns (and its `ETag`) without touching any version.
- **A label cannot cross prompts.** The label's `(prompt_id, version_id)` references `prompt_versions (prompt_id, id)`, so the database refuses a label that points at another prompt's version.
- **`latest` is implicit.** The highest version is always `latest`; it cannot be stored as a label (a CHECK refuses it), so it can never fall out of date.
- **Deleting is whole-prompt.** A single version cannot be deleted. Deleting the prompt cascades its versions and labels (a `DELETE`, which the trigger does not block), and is refused while an evaluator or experiment references one of its versions.

## Consequences

- **Reproducibility.** A span, an experiment item or a judge score linked to a version id always resolves to the template that was actually used. Spans keep the id without a foreign key, so a deleted prompt leaves the link visible but unresolved rather than blocking the delete.
- **Rollback is a label move.** Going back to a previous template is one `PUT` of a label, takes effect for clients on their next fetch (at most the 60-second cache), and needs no new version.
- **Versions accumulate.** Nothing prunes old versions; a busy prompt grows by one row per edit. A per-prompt daily cap of 100 new versions keeps a misbehaving script from filling the table.
- **No in-place fixes.** A typo in a published version is fixed by a new version and a label move, never by editing. History shows both.
- **Schema changes to versions need care.** A migration that has to backfill a column of `prompt_versions` must drop and recreate the trigger around the backfill in the same transaction.
