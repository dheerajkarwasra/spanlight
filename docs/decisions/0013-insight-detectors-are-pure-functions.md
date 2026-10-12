# 13. Insight detectors are pure functions; explanations are advisory

Date: 2026-10-10 · Status: accepted

## Context

The Doctor turns stored spans into insights: an error spike, a retry storm, a tool loop, a client timeout that is shorter than the model's p95. Each one has to name a problem, show the traces behind it and say what to change.

The input is data Spanlight already holds, so the work is deciding what a pattern means. Two ways to do that were considered. An **LLM could read the traces** and write up what it sees. That covers shapes nobody thought of, but its output is not deterministic: the same hour of traffic can produce different findings on different runs, an insight could appear and vanish without the data changing, nothing can be tested against a fixture, and every project run would cost provider calls and tokens. **Rules over the stored spans** are narrower, but a rule gives the same answer for the same rows and can be read, argued with and tested.

## Decision

Detect with rules, and keep every rule a pure function of a loaded context.

- **One loaded context.** The engine reads a project's last 24 hours of llm and tool spans (joined to their traces), the baselines of the previous 7 days and the organization's provider errors once per run, and builds a `DetectorContext`. A detector receives it already cut to its own window (`sliced`) and returns a list of findings.
- **Pure.** A detector runs no query, reads no clock (it uses `ctx.now`), calls no model and uses no randomness. The same context always gives the same findings. Below its minimum sample it returns nothing, so a quiet project is never flagged on a handful of calls.
- **Findings carry values, not prose.** A finding holds its kind, severity, fingerprint key, evidence and the formatted values for its copy. Titles, summaries, fixes and checks live in one catalogue, written once, so the wording of an insight never depends on which detector module produced it.
- **Identity is a fingerprint.** `fingerprint(project, kind, key)` is the same on every run, so a problem that is detected again updates its insight instead of opening a second one, and a problem that stops is resolved after 24 hours without a recurrence.
- **Thresholds are code constants.** Windows, minimum samples and severity limits are written next to the rule that uses them and shown in the documentation. There is no per-project tuning in this phase.
- **Explanations are opt-in and advisory.** A person can ask Claude to explain one insight. The request is a user action, never part of detection; it is capped by a budget; and its answer is labelled as advisory next to the measured evidence. An explanation never changes an insight's status, severity or fingerprint.
- **Every kind says how sure it is.** The catalogue marks a kind as measured (the finding restates observed numbers) or inferred (a pattern that strongly suggests the cause), and the page shows which.

## Consequences

- **Detectors can be tested with fixtures.** A test only needs a handful of span rows, a call to `run` and a comparison of the findings, with no database, clock or network; the threshold, one call below the minimum sample and a fault-injected span are each a few lines. These tests are not yet written.
- **No model in the hot path.** Detection costs one set of queries per project every 15 minutes and no provider spend. Insights keep appearing when no provider key is configured.
- **Coverage is only what has been written.** A problem without a rule produces no insight. Adding one means a new detector module and a catalogue entry, not a prompt.
- **Thresholds are not tuned per project.** A project with unusual traffic can see a finding it considers normal, or miss one. It can mute an insight; changing a threshold needs a release.
- **Advice is canned.** The suggested fix and the check that it worked are the same text for every project. The optional explanation is where project-specific reasoning comes in, and it is labelled as advisory because it can be wrong.
- **The context is bounded.** Each span list is capped, and a run that hit a cap records it, so a very busy project is judged on its newest spans rather than all of them.

## Explanations

An owner or admin can ask Claude to explain one insight. The detectors stay the only source of findings; an explanation is a reading of a finding someone already has in front of them.

- **Opt-in and admin-only.** It runs only when a person with `insights:manage` presses the button. Detection never calls a model, and viewers and members see stored explanations without being able to ask for new ones.
- **The organization's own key.** The call goes through the gateway in process with the organization's oldest Anthropic credential, on a route built for the call (one target, no fallbacks, no cache, no faults). It is traced into the project like any other gateway call, in environment `doctor` with the tag `doctor-explain`, because it is real spend in that project.
- **Budget-capped, race-free.** `EXPLAIN_MONTHLY_BUDGET_USD` caps an organization's explanations per UTC month; `0` turns the feature off. Before the call, a transaction under an advisory lock per organization checks the month's spend plus the call's worst case (the prompt's characters ÷ 3 input tokens and 600 output tokens) and inserts a reservation row for that worst case. The provider is called once, outside any transaction; the row is then completed with the real cost (kept without an answer when the provider billed a call that failed, and at the worst case when the client went away mid-call), or deleted when the call failed unbilled. Settling is shielded from the request's cancellation. Concurrent requests cannot overspend, and a reservation left by a process that died is completed by the cleanup job after 10 minutes at its worst-case cost (never deleted, since the provider may have billed it). A model without a price, or with a price of zero, is refused, since its spend could not be counted.
- **What is sent.** The insight's copy, metrics and window, the catalogue's fix and check, and at most five trace excerpts of 2 KB each: model, status, error class, status message, finish reason, token counts and the first 1 KB of the input and output. Every string goes through the same secret redaction as ingestion first, and a project that does not capture payloads has none to send.
- **Advisory.** The answer is stored as plain text and shown as text, never rendered as HTML or Markdown, under a label saying it was generated by Claude from the evidence and should be verified. It never changes an insight's status, severity or fingerprint.
- **Configurable model.** `EXPLAIN_MODEL` names the model (default `claude-sonnet-5-5`); it must have a price.

Consequences: an explanation costs real money in the organization's own provider account, bounded by the monthly cap; it can be wrong, which is why it is labelled and kept next to the measured evidence; and a prompt holds up to five redacted excerpts of the project's own traces, sent to the provider the organization already uses for those traces.
